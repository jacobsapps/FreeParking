"""A restartable per-window workflow. No OS calls and no display-dependent logic."""
import uuid
from .model import ParkingError, NotSubmitted, Phase, Uncertain, Window
from .verifier import Verifier
from .settings import Settings, continuation_message


class Workflow:
    def __init__(self, store, observer, gate):
        self.store, self.observer, self.gate = store, observer, gate

    def note(self, record, error):
        # Keep phase and intent: error strings never decide what happens next.
        record['issue'] = str(error)
        self.store.save(record)
        return record

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
            if any(t.provider != 'shell' and t.activity == 'unknown' for t in window.tabs):
                raise ParkingError('Cannot confirm agent activity. Nothing interrupted.')
            record['source'] = window.encode()
            record['interruptions'] = {}  # A cancelled earlier attempt grants no future auto-send.
            record['close_interruptions'] = {}
            self.store.save(record)
            for index, tab in enumerate(window.tabs):
                if tab.provider == 'shell': continue
                if tab.activity != 'running': continue
                provider = self.gate.providers.get(tab.provider)
                prepare = getattr(provider, 'prepare_close_interruption', None)
                if prepare:
                    record['close_interruptions'][str(index)] = prepare(tab)
                    self.store.save(record)
                    continue
                receipt = self.store.transition(record, Phase.INTERRUPTING, {'kind':'interrupt', 'index':index})
                try:
                    outcome = self.gate.interrupt(receipt)
                    record.setdefault('interruptions', {})[str(index)] = outcome
                    self.store.transition(record, Phase.SAVED)
                except Exception as error:
                    return self.note(record, error)  # Keep uncertain interruption, never replay.
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
                    if isinstance(error, Uncertain):
                        # The AppleEvent may still be showing a confirmation.
                        # A timeout is not cancellation and must not permit replay.
                        self.note(record, 'Close is still unconfirmed. Check the iTerm confirmation; recovery is saved. ' + str(error))
                    else:
                        self.store.transition(record, Phase.SAVED, issue=str(error) if error else 'Close cancelled; the original window is still open.')
                else:
                    self.note(record, error or Uncertain('Some original tabs changed or remain open. Check again before another action.'))
            except Exception as caught:
                self.note(record, caught)
            return record

    def confirm_closed(self, record):
        source = Window.decode(record['source'])
        for index, tab in enumerate(source.tabs):
            provider = self.gate.providers.get(tab.provider)
            if hasattr(provider,'finish_close') and not provider.client_closed(tab):
                receipt=self.store.transition(record,Phase.CLOSING,dict(kind='stop_client',index=index))
                self.gate.stop_closed_client(receipt)
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
                self.gate.native_archive(receipt)
                record.setdefault('native_archives', {})[str(index)] = True
                self.store.transition(record, Phase.CLOSING, {'kind':'close'})
        self.store.transition(record, Phase.PARKED)

    def reconcile_record(self, record, snapshot):
        source = Window.decode(record['source'])
        intent = record['intent']
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
            self.confirm_closed(record)
        elif record['phase'] in (Phase.OPENING.value, Phase.CHECKING.value, Phase.SAVED.value) and Verifier.returned(record, snapshot):
            self.store.transition(record, Phase.RETURNED)
        return record

    def reconcile(self, record_id):
        initial = self.store.load(record_id)
        with self.store.operation(initial['source']['window_id']):
            record = self.store.load(record_id)
            return self.reconcile_record(record, self.observer.observe())

    def reopen(self, record_id):
        initial = self.store.load(record_id)
        with self.store.operation(initial['source']['window_id']):
            record = self.store.load(record_id)
            snapshot = self.observer.observe()
            self.reconcile_record(record, snapshot)
            if record['phase'] in (Phase.CLOSING.value, Phase.INTERRUPTING.value) and record['intent']:
                return record  # A pending close cannot be called returned or reopened.
            if Verifier.returned(record, snapshot):
                if record['phase'] != Phase.RETURNED.value:
                    self.store.transition(record, Phase.RETURNED)
                return record
            if record['phase'] == Phase.RETURNED.value:
                raise ParkingError('This backup has already returned. It is retained, not automatically reopened.')
            if record['intent']:
                return self.note(record, Uncertain('An earlier operation has no confirmed result. Nothing was repeated; check the disposable terminal.'))
            if not Verifier.source_absent(record, snapshot):
                return self.note(record, Uncertain('Original tabs are still open but changed. No duplicate window was created.'))
            source = Window.decode(record['source'])
            allowed = {t['terminal_id'] for t in record['targets'].values()}
            if any(Verifier.other_conversation_tabs(t, snapshot, allowed) for t in source.tabs):
                return self.note(record, Uncertain('A saved conversation is already open elsewhere. No duplicate or continuation was launched.'))
            for tab in source.tabs:
                self.gate.providers.get(tab.provider).launch(tab, str(uuid.uuid4()))  # Validate every folder before any create.
            # A missing middle tab needs explicit rearrangement support. Never
            # silently append it in the wrong order and claim a verified return.
            for index, target in record['targets'].items():
                if target['terminal_id'] not in snapshot.terminal_ids:
                    return self.note(record, Uncertain('A previously returned tab closed. Recovery is retained; no automatic duplicate/reordering attempted.'))
            wid = next((t['window_id'] for t in record['targets'].values()), None)
            settings = Settings(self.store.root).read()
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
                        return self.note(self.store.load(record_id), error)
                token = str(uuid.uuid4())
                intent = dict(kind='create', index=index, receipt=token, window_id=wid)
                message = continuation_message(tab, record.get('interruptions', {}).get(str(index)), settings)
                if message is not None:
                    intent['continue_message'] = message  # Durable before exec; never re-send on retry.
                receipt = self.store.transition(record, Phase.OPENING, intent)
                try:
                    created = self.gate.create(receipt)
                    wid, sid = created['windowId'], created['sessionId']
                    if not isinstance(wid, str) or not wid or not isinstance(sid, str) or not sid:
                        raise Uncertain('The terminal returned an invalid create receipt.')
                    record['targets'][str(index)] = dict(window_id=wid, terminal_id=sid, receipt=token)
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
            settled = self.gate.settle(lambda observed: Verifier.returned(record, observed))
            self.reconcile_record(record, settled)
            return record
