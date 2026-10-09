"""All mutating calls pass through a durable-receipt and identity gate.

A separate outer test shim protects the working desktop. This inner gate
protects lifecycle ordering even when an adapter or coordinator is defective.
"""
from .model import ParkingError, NotSubmitted, Phase, Window
from .verifier import Verifier


class Gate:
    def __init__(self, store, observer, terminal, providers, mutations_enabled=False, settle=None):
        self.store, self.observer, self.terminal, self.providers = store, observer, terminal, providers
        self.mutations_enabled = mutations_enabled
        self.settle = settle or (lambda ready: self.observer.observe())

    def record(self, receipt):
        if not self.mutations_enabled:
            raise ParkingError('Mutations require an explicit live-app or registered disposable-test capability.')
        return self.store.verify(receipt)

    def interrupt(self, receipt):
        def authorize(session_id=None, turn_id=None):
            record = self.record(receipt)
            intent = record['intent']
            if record['phase'] != Phase.INTERRUPTING.value or not intent or intent['kind'] != 'interrupt':
                raise ParkingError('Interruption requires its own durable intent.')
            source = Window.decode(record['source'])
            provider_guard = getattr(self.terminal, 'authorize_provider', None)
            if provider_guard: provider_guard(source)
            tab = source.tabs[intent['index']]
            if session_id is not None and (session_id != tab.session_id or turn_id != tab.turn_id):
                raise ParkingError('Interruption permit belongs to another conversation/turn.')
            if not Verifier.source_open(record, self.observer.observe()):
                raise ParkingError('Window/session changed before interruption. Nothing stopped.')
            return tab
        tab = authorize()
        return self.providers.get(tab.provider).interrupt(tab, authorize=authorize)

    def stop_closed_client(self, receipt):
        def authorize():
            record = self.record(receipt);intent = record['intent']
            if record['phase'] != Phase.CLOSING.value or not intent or intent['kind'] != 'stop_client':
                raise ParkingError('Client shutdown requires a durable close receipt.')
            source = Window.decode(record['source'])
            if not Verifier.source_absent(record,self.observer.observe()):
                raise ParkingError('Never stop a client while its original terminal is still open.')
            guard = getattr(self.terminal,'authorize_provider',None)
            if guard: guard(source)
            return source.tabs[intent['index']]
        tab=authorize()
        self.providers.get(tab.provider).finish_close(tab,authorize)

    def native_archive(self, receipt):
        def authorize(sid):
            record = self.record(receipt)
            intent = record['intent']
            if not intent or intent['kind'] != 'native_archive':
                raise ParkingError('Native archiving requires a durable per-conversation intent.')
            source = Window.decode(record['source']);tab = source.tabs[intent['index']]
            if tab.session_id != sid or tab.provider != 'codex':
                raise ParkingError('Native archive permit belongs to another conversation.')
            expected = Phase.CLOSING.value if intent['archived'] else Phase.CHECKING.value
            snapshot = self.observer.observe()
            if Verifier.other_conversation_tabs(tab, snapshot):
                raise ParkingError('This conversation is already open elsewhere. Its native runtime was left alone.')
            if record['phase'] != expected or not Verifier.source_absent(record,snapshot):
                raise ParkingError('Original tabs must be closed before changing native archive state.')
            provider_guard = getattr(self.terminal, 'authorize_provider', None)
            if provider_guard: provider_guard(source)
            return tab, intent['archived']
        record = self.record(receipt)
        source = Window.decode(record['source']);tab = source.tabs[record['intent']['index']]
        tab, archived = authorize(tab.session_id)
        return self.providers.get(tab.provider).set_archived(tab, archived, authorize)

    def close(self, receipt):
        record = self.record(receipt)
        if record['phase'] != Phase.CLOSING.value or record['intent'] != {'kind': 'close'}:
            raise ParkingError('Closing requires a durable close intent.')
        snapshot = self.observer.observe()
        if not Verifier.source_open(record, snapshot):
            raise ParkingError('The window or its processes changed since saving. Nothing closed.')
        source = Window.decode(record['source'])
        live = snapshot.window(source.window_id)
        for index, tab in enumerate(live.tabs):
            if tab.provider == 'shell': continue
            if tab.activity == 'unknown':
                raise ParkingError('Agent activity is unknown. Nothing closed.')
            if tab.activity == 'running':
                checkpoint = record.get('close_interruptions', {}).get(str(index))
                if not checkpoint:
                    raise ParkingError('An agent started working before close. Nothing closed; refresh and park again.')
                self.providers.get(tab.provider).validate_close_interruption(tab, checkpoint)
        return self.terminal.call('close', windowId=source.window_id,
                                  sessionIds=[t.terminal_id for t in source.tabs])

    def create(self, receipt):
        try:
            request = self.prepare_create(receipt)
            self.record(receipt)  # Recheck durability after the settling wait.
        except ParkingError as error:
            raise NotSubmitted(str(error)) from error
        # Exceptions beyond this boundary remain uncertain, never auto-replayed.
        return self.terminal.call('create', **request)

    def prepare_create(self, receipt):
        record = self.record(receipt)
        intent = record['intent']
        if record['phase'] != Phase.OPENING.value or not intent or intent['kind'] != 'create':
            raise ParkingError('Creating a tab requires a durable launch intent.')
        source = Window.decode(record['source'])
        snapshot = self.settle(lambda observed: all(Verifier.matching_tab(source.tabs[int(i)], target, observed)
                              for i, target in record['targets'].items()))
        if not Verifier.source_absent(record, snapshot):
            raise ParkingError('Original tabs remain open. Nothing was duplicated.')
        if Verifier.receipt_target(intent['receipt'], snapshot):
            raise ParkingError('The intended launch already exists. Reconcile it instead of creating again.')
        wid = intent.get('window_id')
        ids = []
        if wid:
            targets = [record['targets'][str(i)] for i in range(intent['index'])]
            if any(t['window_id'] != wid for t in targets):
                raise ParkingError('Returned tabs have split across windows.')
            raw = snapshot.raw_window(wid)
            ids = [t['terminal_id'] for t in targets]
            actual = [s['id'] for t in raw['tabs'] for s in t['sessions']] if raw else []
            if not all(Verifier.matching_tab(source.tabs[i], t, snapshot) for i, t in enumerate(targets)):
                raise ParkingError('A previously returned conversation changed. No tab was appended.')
            if ids != actual:
                raise ParkingError('The returned window changed. No tab was appended.')
        tab = source.tabs[intent['index']]
        allowed = {t['terminal_id'] for t in record['targets'].values()}
        if Verifier.other_conversation_tabs(tab, snapshot, allowed):
            raise ParkingError('This conversation was already reopened elsewhere. No duplicate or continuation was launched.')
        provider = self.providers.get(tab.provider)
        prompt = intent.get('continue_message')
        command = provider.launch(tab, intent['receipt'], prompt=prompt) if prompt is not None else provider.launch(tab, intent['receipt'])
        return dict(windowId=wid, sessionIds=ids, command=command)
