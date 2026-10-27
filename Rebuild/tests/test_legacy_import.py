from dataclasses import asdict
import json
import unittest
import uuid
import test_core
from parking.legacy import import_parked


class LegacyImport(unittest.TestCase):
    def setUp(self):
        test_core.Fixture.setUp(self)
        self.legacy=self.root/'old';self.legacy.mkdir()
        self.ident=str(uuid.uuid4());self.path=self.legacy/(self.ident+'.json')
        self.value=dict(id=self.ident,schema_version=1,status='parked',created_at='2026-01-01T00:00:00Z',
            source_window_id='fixture',source_session_ids=[t.terminal_id for t in self.desktop.source.tabs],
            tabs=[dict(**asdict(t),index=i,issue='',resume_command='DO NOT EXECUTE SAVED COMMAND') for i,t in enumerate(self.desktop.source.tabs)],
            restored_targets={},pending_tab=None)
        self.path.write_text(json.dumps(self.value))

    def test_import_preserves_original_and_is_idempotent(self):
        original=self.path.read_bytes()
        self.assertEqual(import_parked(self.store,self.legacy),[])
        self.assertEqual(import_parked(self.store,self.legacy),[])
        records=self.store.all();self.assertEqual(len(records),1)
        self.assertEqual(records[0]['phase'],'parked')
        self.assertEqual(records[0]['legacy_id'],self.ident)
        self.assertEqual(len(records[0]['source']['tabs']),3)
        self.assertNotIn('DO NOT EXECUTE',json.dumps(records))
        self.assertEqual(self.path.read_bytes(),original)

    def test_ambiguous_old_create_is_visible_and_never_replayed(self):
        self.value['status']='restoring';self.value['pending_tab']=1
        self.path.write_text(json.dumps(self.value))
        warnings=import_parked(self.store,self.legacy)
        self.assertIn('unfinished operation',warnings[0]);self.assertEqual(self.store.all(),[])
