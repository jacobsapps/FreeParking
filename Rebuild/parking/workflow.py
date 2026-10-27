"""A restartable per-window workflow. No OS calls and no display-dependent logic."""
from .diagnostics import timed, event

import datetime
import uuid
import time
from .model import ParkingError, NotSubmitted, Phase, Uncertain, Window
from .verifier import Verifier
from .settings import Settings, continuation_message


class Workflow:
    def __init__(self, store, observer, gate):
        self.store, self.observer, self.gate = store, observer, gate

    def note(self, record, error):
        # Keep phase and intent: error strings never decide what happens next.
        record['issue'] = str(error)
        record['issue_kind'] = ('not_submitted' if isinstance(error, NotSubmitted) else
                                'uncertain' if record['intent'] or isinstance(error, Uncertain) else 'failed')
        # Retain the original failure even if a later retry reports another issue.
        record['events'].append(dict(phase=record['phase'], intent=record['intent'],
            issue=str(error), error_type=type(error).__name__,
            at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        if isinstance(getattr(error, 'details', None), dict):
            record['events'][-1]['details'] = error.details
        self.store.save(record)
        return record

    @timed('workflow.park')
    def park(self, window_id, expected_fingerprint):
        with self.store.operation(window_id):
            snapshot = self.observer.observe()
            window = snapshot.window(window_id)
            if not window:
                raise ParkingError(snapshot.issues.get(window_id, 'Window is no longer present.'))
            if window.fingerprint != expected_fingerprint:
                raise ParkingError('The window changed since inspection. Refresh before parking.')
            if any(Verifier.other_conversation_tabs(t, snapshot, {t.terminal_id}) for t in window.tabs):
                raise ParkingError('A conversation is also open in another tab. No shared task was interrupted.')
            record, _ = self.store.begin(window)
            if record['phase'] != Phase.SAVED.value or record['intent']:
                raise ParkingError('An earlier operation needs reconciliation before parking again.')
            if any(t.provider == 'codex' and t.activity == 'unknown' for t in window.tabs):
                raise ParkingError('Cannot confirm agent activity. Nothing interrupted.')
            for tab in window.tabs:
                provider = self.gate.providers.get(tab.provider)
                provider.launch(tab, str(uuid.uuid4()))
                preflight = getattr(provider, 'preflight', None)
                if preflight: preflight(tab)
            record['source'] = window.encode()
            # A retry may retain continuation only when the provider re-observes
            # that exact interrupted turn as its latest persisted request.
            previous = record.get('interruptions', {})
            kept = {}
            for i, tab in enumerate(window.tabs):
                outcome = previous.get(str(i))
                provider = self.gate.providers.get(tab.provider)
                current = getattr(provider, 'interruption_current', None)
                if outcome and tab.activity == 'idle' and current:
                    try: eligible = current(tab, outcome)
                    except Exception: eligible = False  # Optional message evidence, not session identity.
                    if eligible: kept[str(i)] = outcome
            record['interruptions'] = kept
            record['close_interruptions'] = {}
            self.store.save(record)
            for index, tab in enumerate(window.tabs):
                if tab.provider == 'shell': continue
                if tab.activity != 'running': continue
                provider = self.gate.providers.get(tab.provider)
                prepare = getattr(provider, 'prepare_close_interruption', None)
                if prepare:
                    try:
                        checkpoint = prepare(tab)
                    except Exception:
                        checkpoint = None  # No continuation evidence is not lost session recovery.
                    if checkpoint is not None:
                        record['close_interruptions'][str(index)] = checkpoint
                        self.store.save(record)  # Journal failures are never optional.
                    continue
                receipt = self.store.transition(record, Phase.INTERRUPTING, {'kind':'interrupt', 'index':index})
                try:
                    outcome = self.gate.interrupt(receipt)
                    record.setdefault('interruptions', {})[str(index)] = outcome
                    self.store.transition(record, Phase.SAVED)
                except Exception as error:
                    record = self.store.load(record['id'])
                    if isinstance(error, NotSubmitted):
                        self.store.transition(record, Phase.SAVED)
                    return self.note(record, error)  # Unknown submissions remain pending, never replayed.
            receipt = self.store.transition(record, Phase.CLOSING, {'kind': 'close'})
            error = None
            try:
                self.gate.close(receipt)
            except Exception as caught:
                error = caught
            # A lost acknowledgement is not proof of a failed close.
            try:
                after = self.observer.observe()
                if Verifier.source_absent(record, after):
                    self.confirm_closed(record)
                elif Verifier.source_open(record, after):
                    if error is not None and not isinstance(error, NotSubmitted):
                        # The AppleEvent may still be showing a confirmation.
                        # A timeout is not cancellation and must not permit replay.
                        self.note(record, 'Close is still unconfirmed. Check the iTerm confirmation; recovery is saved. ' + str(error))
                    else:
                        if error is None: record['interruptions'] = {}  # Cancellation grants no future auto-send.
                        self.store.transition(record, Phase.SAVED, issue=str(error) if error else 'Close cancelled; the original window is still open.')
                else:
                    self.note(record, error or Uncertain('Some original tabs changed or remain open. Check again before another action.'))
            except Exception as caught:
                self.note(record, caught)
            return record

    @timed('workflow.confirm_closed')
    def confirm_closed(self, record):
        source = Window.decode(record['source'])
        for index, tab in enumerate(source.tabs):
            provider = self.gate.providers.get(tab.provider)
            if hasattr(provider,'finish_close') and not provider.client_closed(tab):
                receipt=self.store.transition(record,Phase.CLOSING,dict(kind='stop_client',index=index))
                try:
                    self.gate.stop_closed_client(receipt)
                except NotSubmitted:
                    self.store.transition(record,Phase.CLOSING,{'kind':'close'})
                    raise
                self.store.transition(record,Phase.CLOSING,{'kind':'close'})
        for index, checkpoint in record.get('close_interruptions', {}).items():
            if index in record.get('interruptions', {}): continue
            tab = source.tabs[int(index)]
            outcome = self.gate.providers.get(tab.provider).confirm_close_interruption(tab, checkpoint)
            record.setdefault('interruptions', {})[index] = outcome
            self.store.save(record)
        for index, tab in enumerate(source.tabs):
            provider = self.gate.providers.get(tab.provider)
            if hasattr(provider, 'set_archived') and record.get('native_archives', {}).get(str(index)) is not True:
                receipt = self.store.transition(record, Phase.CLOSING,
                    dict(kind='native_archive', index=index, archived=True))
                try:
                    self.gate.native_archive(receipt)
                except NotSubmitted as error:
                    self.store.transition(record, Phase.CLOSING, {'kind':'close'})
                    raise error
                record.setdefault('native_archives', {})[str(index)] = True
                self.store.transition(record, Phase.CLOSING, {'kind':'close'})
        self.store.transition(record, Phase.PARKED)

    def reconcile_record(self, record, snapshot, finish_closing=True):
        source = Window.decode(record['source'])
        intent = record['intent']
        if intent and intent['kind'] == 'interrupt':
            tab = source.tabs[intent['index']]
            observe = getattr(self.gate.providers.get(tab.provider), 'observe_interruption', None)
            outcome = observe(tab) if observe else None
            if outcome is None: return record
            record.setdefault('interruptions', {})[str(intent['index'])] = outcome
            self.store.transition(record, Phase.SAVED)
            return record  # Explicit Park retry revalidates current turns before closing.
        if intent and intent['kind'] == 'stop_client':
            tab=source.tabs[intent['index']]
            if not self.gate.providers.get(tab.provider).client_closed(tab): return record
            self.store.transition(record,Phase.CLOSING,{'kind':'close'})
            intent=record['intent']
        if intent and intent['kind'] == 'native_archive':
            tab = source.tabs[intent['index']]
            if self.gate.providers.get(tab.provider).is_archived(tab) != intent['archived']:
                return record  # An ambiguous submitted mutation is never repeated.
            record.setdefault('native_archives', {})[str(intent['index'])] = intent['archived']
            if intent['archived']:
                self.store.transition(record, Phase.CLOSING, {'kind':'close'})
            else:
                self.store.transition(record, Phase.CHECKING)
            intent = record['intent']
        if intent and intent['kind'] == 'create':
            target = Verifier.receipt_target(intent['receipt'], snapshot)
            if target:
                record['targets'][str(intent['index'])] = target
                self.store.transition(record, Phase.CHECKING)
        if record['phase'] in (Phase.CLOSING.value, Phase.SAVED.value) and Verifier.source_absent(record, snapshot):
            if finish_closing: self.confirm_closed(record)
            elif record['phase'] == Phase.CLOSING.value and record['intent'] == {'kind':'close'}:
                if not record.get('close_confirmed'):
                    record['close_confirmed'] = True
                    self.store.save(record)
        elif record['phase'] in (Phase.OPENING.value, Phase.CHECKING.value) and Verifier.returned(record, snapshot):
            self.store.transition(record, Phase.RETURNED)
        return record

    @timed('workflow.reconcile')
    def reconcile(self, record_id, read_only=False, snapshot=None):
        if snapshot is not None and not read_only:
            raise ParkingError('Only a read-only refresh may share its inventory.')
        initial = self.store.load(record_id)
        with self.store.operation(initial['source']['window_id']):
            record = self.store.load(record_id)
            return self.reconcile_record(record, snapshot if snapshot is not None else self.observer.observe(), finish_closing=not read_only)

    @timed('workflow.finish_parking')
    def finish_parking(self, record_id):
        """Explicitly finish an already-closed window, never close/reopen a tab."""
        initial = self.store.load(record_id)
        with self.store.operation(initial['source']['window_id']):
            record = self.store.load(record_id)
            if record['phase'] == Phase.PARKED.value: return record
            snapshot = self.observer.observe()
            if record['phase'] == Phase.SAVED.value and not record['intent'] and Verifier.source_absent(record, snapshot):
                self.store.transition(record, Phase.CLOSING, dict(kind='close'))
            if (record['phase'] != Phase.CLOSING.value or record['intent'] != {'kind':'close'}
                    or not Verifier.source_absent(record, snapshot)):
                raise ParkingError('Only a confirmed closed window can finish parking. No terminal was changed.')
            try:
                self.confirm_closed(record)
            except Exception as error:
                record = self.store.load(record_id)
                self.note(record, error)
            return record

    @timed('workflow.restore')
    def reopen(self, record_id):
        initial = self.store.load(record_id)
        with self.store.operation(initial['source']['window_id']):
            record = self.store.load(record_id)
            snapshot = self.observer.observe()
            self.reconcile_record(record, snapshot, finish_closing=False)
            if record['phase'] == Phase.SAVED.value and Verifier.source_open(record, snapshot):
                return self.note(record, NotSubmitted('The saved original window is still open. Choose Finish parking to close it; no duplicate was opened.'))
            if record['phase'] in (Phase.CLOSING.value, Phase.INTERRUPTING.value) and record['intent']:
                return record  # A pending close cannot be called returned or reopened.
            if Verifier.returned(record, snapshot):
                if record['phase'] != Phase.RETURNED.value:
                    self.store.transition(record, Phase.RETURNED)
                return record
            if record['phase'] == Phase.RETURNED.value:
                raise ParkingError('This backup has already returned. It is retained, not automatically reopened.')
            if record['intent']:
                if record.get('issue'): return record  # Preserve the actual cause, not a generic retry error.
                return self.note(record, Uncertain('An earlier operation has no confirmed result. Nothing was repeated; recovery is retained.'))
            if not Verifier.source_absent(record, snapshot):
                return self.note(record, Uncertain('Original tabs are still open but changed. No duplicate window was created.'))
            source = Window.decode(record['source'])
            allowed = {t['terminal_id'] for t in record['targets'].values()}
            if any(Verifier.other_conversation_tabs(t, snapshot, allowed) for t in source.tabs):
                return self.note(record, Uncertain('A saved conversation is already open elsewhere. No duplicate or continuation was launched.'))
            for tab in source.tabs:
                self.gate.providers.get(tab.provider).launch(tab, str(uuid.uuid4()))  # Validate every folder before any create.
            # An explicit retry can recover a closed returned tab. Check a fresh
            # complete inventory twice and keep its old receipt for diagnosis.
            missing = {i:t for i,t in record['targets'].items() if t['terminal_id'] not in snapshot.terminal_ids}
            if missing:
                snapshot = self.observer.observe()
                for index, target in missing.items():
                    if target['terminal_id'] in snapshot.terminal_ids: continue
                    if Verifier.other_conversation_tabs(source.tabs[int(index)], snapshot, allowed):
                        return self.note(record, Uncertain('A previously returned conversation is open elsewhere. No duplicate was launched.'))
                    record.setdefault('closed_targets', []).append(dict(index=int(index), **target))
                    del record['targets'][index]
                    # Continuation belongs to the first launch, never a recovery
                    # of a tab the user subsequently closed.
                    record.get('interruptions', {}).pop(index, None)
                self.store.save(record)
            wid = next((t['window_id'] for t in record['targets'].values()), None)
            settings = Settings(self.store.root).read_optional()
            for index, tab in enumerate(source.tabs):
                if str(index) in record['targets']:
                    continue
                if record.get('native_archives', {}).get(str(index)) is True:
                    receipt = self.store.transition(record, Phase.CHECKING,
                        dict(kind='native_archive', index=index, archived=False))
                    try:
                        self.gate.native_archive(receipt)
                        record['native_archives'][str(index)] = False
                        self.store.transition(record, Phase.CHECKING)
                    except Exception as error:
                        record = self.store.load(record_id)
                        if isinstance(error, NotSubmitted):
                            self.store.transition(record, Phase.CHECKING)
                            return self.note(record, NotSubmitted(
                                'Tab '+str(index+1)+' (“'+tab.title+'”): restore preparation failed before any unarchive request was sent. '
                                'Already opened tabs were kept. You can retry reopening. '+str(error)))
                        return self.note(record, error)
                token = str(uuid.uuid4())
                intent = dict(kind='create', index=index, receipt=token, window_id=wid)
                interruption = record.get('interruptions', {}).get(str(index))
                message = continuation_message(tab, interruption, settings)
                current = getattr(self.gate.providers.get(tab.provider), 'interruption_current', None)
                if message is not None and current:
                    try: eligible = current(tab, interruption)
                    except Exception: eligible = False
                    if not eligible: message = None  # Reopen the session without optional text.
                if message is not None:
                    intent['continue_message'] = message  # Durable before exec; never re-send on retry.
                receipt = self.store.transition(record, Phase.OPENING, intent)
                try:
                    for attempt in range(3):
                        try:
                            created = self.gate.create(receipt)
                            break
                        except NotSubmitted as refused:
                            if attempt == 2: raise
                            # A definite pre-submission refusal is safe to retry
                            # after a fresh observation. Never retry Uncertain.
                            self.note(record, refused)
                            receipt = self.store.transition(record, Phase.OPENING, intent)
                            time.sleep(0.2 * (attempt + 1))
                    wid, sid = created['windowId'], created['sessionId']
                    if not isinstance(wid, str) or not wid or not isinstance(sid, str) or not sid:
                        raise Uncertain('The terminal returned an invalid create receipt.')
                    record['targets'][str(index)] = dict(window_id=wid, terminal_id=sid, receipt=token)
                    if created.get('presentationWarnings'):
                        record.setdefault('presentation_warnings', []).extend(created['presentationWarnings'])
                    self.store.transition(record, Phase.CHECKING)
                except Exception as error:
                    # Reload durable state: a save could have committed before
                    # readback failed. Never improvise another create here.
                    record = self.store.load(record_id)
                    if isinstance(error, NotSubmitted):
                        self.store.transition(record, Phase.CHECKING, issue=str(error))
                    try:
                        self.reconcile_record(record, self.observer.observe())
                    except Exception:
                        pass
                    if record['phase'] != Phase.RETURNED.value:
                        self.note(record, error)
                    return record
            settled = self.gate.settle(lambda observed: all(Verifier.matching_tab(
                source.tabs[int(i)], target, observed) for i,target in record['targets'].items()))
            if not Verifier.returned(record, settled) and all(Verifier.matching_tab(
                    source.tabs[int(i)], target, settled) for i,target in record['targets'].items()):
                receipt = self.store.transition(record, Phase.CHECKING, dict(kind='reorder'))
                try:
                    self.gate.reorder(receipt)
                    self.store.transition(record, Phase.CHECKING)
                except Exception as error:
                    record = self.store.load(record_id)
                    if isinstance(error, NotSubmitted): self.store.transition(record, Phase.CHECKING)
                    return self.note(record, error)
                settled = self.gate.settle(lambda observed: Verifier.returned(record, observed))
            self.reconcile_record(record, settled)
            return record
