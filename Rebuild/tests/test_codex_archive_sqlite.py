"""Real read-only SQLite archive contracts; only private temporary fixtures.

The production Codex adapter is exercised without replacing is_archived(). RPC
is an in-memory stub; its optional callback writes only this test's temp DB to
model native acknowledgement/readback races. No agent, terminal or service runs.
"""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.agents import Codex
from parking.model import NotSubmitted, ParkingError, Process, Tab, Uncertain


class CodexArchiveSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/"database with spaces ' and #"
        self.root.mkdir()
        self.path = self.root/'state_5.sqlite'
        self.sid = str(uuid.uuid4())
        self.tab = Tab('fixture-tab','/dev/fixture','display only','codex',self.sid,
                       str(self.root),Process(123,'fixture birth','/dev/fixture','/fixture/codex'),'idle')
        self.rpc = Mock()
        self.rpc.read.return_value = dict(id=self.sid,cwd=self.tab.cwd,turns=[])
        self.provider = Codex(Mock(),self.rpc,root=self.root)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0)')
            db.execute('INSERT INTO threads (id,cwd,archived) VALUES (?,?,?)',(self.sid,self.tab.cwd,0))
            db.commit()

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(statement,parameters)
            db.commit()

    def test_exact_row_zero_and_one_are_read_without_database_mutation(self):
        for flag in (0,1):
            self.sql('UPDATE threads SET archived=? WHERE id=?',(flag,self.sid))
            before = hashlib.sha256(self.path.read_bytes()).digest()
            self.assertIs(self.provider.is_archived(self.tab),bool(flag))
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(),before)
        self.rpc.read.assert_not_called(); self.rpc.call.assert_not_called()

    def test_missing_database_is_not_created_by_read(self):
        self.path.unlink()
        with self.assertRaisesRegex(ParkingError,'archive state'): self.provider.is_archived(self.tab)
        self.assertFalse(self.path.exists())

    def test_missing_thread_never_falls_back_to_another_record(self):
        self.sql('UPDATE threads SET id=?',(str(uuid.uuid4()),))
        with self.assertRaisesRegex(ParkingError,'identity/folder'): self.provider.is_archived(self.tab)

    def test_wrong_folder_is_not_an_archive_receipt(self):
        self.sql('UPDATE threads SET cwd=?',(str(self.root/'different'),))
        with self.assertRaisesRegex(ParkingError,'identity/folder'): self.provider.is_archived(self.tab)

    def test_unexpected_archive_values_are_rejected(self):
        for flag in (-1,2,0.5,'unknown',sqlite3.Binary(b'0')):
            with self.subTest(flag=flag):
                self.sql('UPDATE threads SET archived=?',(flag,))
                with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)

    def test_null_or_text_typed_flags_in_incompatible_schema_are_rejected(self):
        self.sql('DROP TABLE threads')
        self.sql('CREATE TABLE threads (id TEXT, cwd TEXT, archived TEXT)')
        for flag in (None,'0','1'):
            with self.subTest(flag=flag):
                self.sql('DELETE FROM threads')
                self.sql('INSERT INTO threads VALUES (?,?,?)',(self.sid,self.tab.cwd,flag))
                with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)

    def test_missing_schema_and_corrupt_database_are_visible_failures(self):
        self.sql('DROP TABLE threads')
        with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)
        self.path.write_bytes(b'not a SQLite database')
        with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)

    def test_denied_database_open_is_not_an_unarchived_result(self):
        # sqlite raises OperationalError for an OS permission denial. Injection
        # is deterministic even if a test runner has elevated filesystem access.
        with patch('sqlite3.connect',side_effect=sqlite3.OperationalError('unable to open database file')):
            with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)
        self.rpc.call.assert_not_called()

    def test_already_desired_state_never_sends_native_mutation(self):
        authorize = Mock()
        self.provider.set_archived(self.tab,False,authorize)
        self.rpc.read.assert_not_called(); self.rpc.call.assert_not_called()
        authorize.assert_not_called()

    def test_archive_and_unarchive_confirm_exact_native_readback(self):
        authorize = Mock()
        def native(method, arguments, *, mutation, authorize):
            self.assertTrue(mutation)
            self.assertEqual(arguments,dict(threadId=self.sid))
            authorize(self.sid,None)
            self.sql('UPDATE threads SET archived=? WHERE id=?',
                     (1 if method=='thread/archive' else 0,self.sid))
            return {}
        self.rpc.call.side_effect = native
        self.provider.set_archived(self.tab,True,authorize)
        self.assertTrue(self.provider.is_archived(self.tab))
        self.provider.set_archived(self.tab,False,authorize)
        self.assertFalse(self.provider.is_archived(self.tab))
        self.assertEqual([c.args[0] for c in self.rpc.call.call_args_list],['thread/archive','thread/unarchive'])
        self.assertEqual([c.args for c in authorize.call_args_list],[(self.sid,),(self.sid,)])

    def test_bad_preflight_database_is_not_submitted(self):
        self.path.unlink()
        with self.assertRaises(NotSubmitted): self.provider.set_archived(self.tab,True,Mock())
        self.rpc.read.assert_not_called(); self.rpc.call.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_wrong_native_thread_or_active_turn_never_submits(self):
        for thread in (dict(id=str(uuid.uuid4()),cwd=self.tab.cwd,turns=[]),
                       dict(id=self.sid,cwd=str(self.root/'other'),turns=[]),
                       dict(id=self.sid,cwd=self.tab.cwd,turns=[dict(id='active',status='inProgress')])):
            with self.subTest(thread=thread):
                self.rpc.read.return_value=thread
                with self.assertRaises(NotSubmitted): self.provider.set_archived(self.tab,True,Mock())
        self.rpc.call.assert_not_called()

    def test_acknowledgement_without_changed_database_remains_uncertain(self):
        self.rpc.call.return_value={}
        with self.assertRaises(Uncertain): self.provider.set_archived(self.tab,True,Mock())
        self.rpc.call.assert_called_once()
        self.assertFalse(self.provider.is_archived(self.tab))

    def test_post_send_database_loss_is_not_misclassified_as_not_submitted(self):
        def native(*args,**kwargs): self.path.unlink(); return {}
        self.rpc.call.side_effect=native
        with self.assertRaises(ParkingError) as caught: self.provider.set_archived(self.tab,True,Mock())
        self.assertNotIsInstance(caught.exception,NotSubmitted)
        self.rpc.call.assert_called_once()
        self.assertFalse(self.path.exists())

    def test_post_send_identity_change_retains_unknown_mutation_classification(self):
        def native(*args,**kwargs): self.sql('UPDATE threads SET cwd=?',(str(self.root/'changed'),)); return {}
        self.rpc.call.side_effect=native
        with self.assertRaises(ParkingError) as caught: self.provider.set_archived(self.tab,True,Mock())
        self.assertNotIsInstance(caught.exception,NotSubmitted)
        self.rpc.call.assert_called_once()

    def assert_provider_connection_closed(self, *, query_fails):
        opened = []
        real_connect = sqlite3.connect
        def tracked(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection
        if query_fails: self.sql('DROP TABLE threads')
        try:
            with patch('sqlite3.connect', side_effect=tracked):
                if query_fails:
                    with self.assertRaises(ParkingError): self.provider.is_archived(self.tab)
                else:
                    self.assertFalse(self.provider.is_archived(self.tab))
            self.assertEqual(len(opened), 1)
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed'):
                opened[0].execute('SELECT 1')
        finally:
            for connection in opened: connection.close()

    def test_read_connection_is_closed_before_success_returns(self):
        self.assert_provider_connection_closed(query_fails=False)

    def test_read_connection_is_closed_when_query_fails(self):
        self.assert_provider_connection_closed(query_fails=True)


if __name__ == '__main__': unittest.main()
