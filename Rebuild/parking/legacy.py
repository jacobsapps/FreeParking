"""Conservative, idempotent import of complete parked v1 saves. Originals stay put."""
import json
from pathlib import Path
import uuid
from .model import ParkingError, Process, Tab, Window, Phase


def import_parked(store, root):
    records, _ = store.readable()
    imported = {r.get('legacy_id') for r in records}
    warnings = []
    for path in sorted(Path(root).glob('*.json')):
        try:
            old = json.loads(path.read_text())
            if old.get('schema_version') != 1: continue
            ident = str(uuid.UUID(old['id']))
            if ident in imported: continue
            if old.get('status') in ('archived','restored'): continue
            if old.get('status') != 'parked' or old.get('restored_targets') or old.get('pending_tab') is not None:
                raise ParkingError('This older save has an unfinished operation; its original file needs review before launching duplicates')
            tabs = []
            for index, saved in enumerate(old['tabs']):
                if saved['index'] != index or saved.get('issue'): raise ParkingError('Unresolved legacy tab')
                p = saved['process']
                process = Process(p['pid'],p['started'],saved['tty'],p['executable'],p.get('ppid',0))
                tabs.append(Tab(saved['terminal_id'],saved['tty'],saved['title'],saved['provider'],
                                saved['session_id'],saved['cwd'],process))
            if old['source_session_ids'] != [t.terminal_id for t in tabs]: raise ParkingError('Legacy identities disagree')
            window = Window(old['source_window_id'],tuple(tabs),old.get('window_bounds') or {})
            with store.operation(window.window_id):
                record, _ = store.begin(window)
                if record['phase'] != 'saved' or record['targets'] or record['intent']:
                    raise ParkingError('Existing recovery must finish first')
                record.update(legacy_id=ident, legacy_path=str(path), created_at=old['created_at'])
                store.transition(record,Phase.PARKED)
            imported.add(ident)
        except Exception as error:
            warnings.append('Previous recovery '+str(path)+': '+str(error)+'. Original file retained.')
    return warnings
