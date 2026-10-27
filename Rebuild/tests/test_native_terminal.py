"""Native targeting and ambiguous-write contracts, without touching iTerm."""
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as N
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from parking.model import Observation, NotSubmitted, Uncertain
from parking.native_terminal import NativeITerm
from parking.terminal_api import TerminalAPI, exact_window
from test_core import Desktop


def window(ids=('one', 'two'), wid='pty-owned'):
    return N(window_id=wid, tabs=[N(sessions=[N(session_id=sid)]) for sid in ids],
             async_close=AsyncMock(), async_set_tabs=AsyncMock())


class NativeTargetTests(unittest.TestCase):
    def test_exact_uuid_and_legacy_alias(self):
        owned=window();request=dict(windowId='42',sessionIds=['one','two'])
        self.assertIs(exact_window([owned],request,{'pty-owned':'42'}),owned)
        with self.assertRaises(ValueError):exact_window([owned],request,{})

    def test_changed_order_extra_missing_split_or_wrong_window_refused(self):
        for ids,wid in [(('two','one'),'pty-owned'),(('one',),'pty-owned'),
                        (('one','two','extra'),'pty-owned'),(('one','two'),'pty-other')]:
            with self.subTest(ids=ids,wid=wid),self.assertRaises(ValueError):
                exact_window([window(ids,wid)],dict(windowId='pty-owned',sessionIds=['one','two']),{})
        split=window();split.tabs[0].sessions.append(N(session_id='pane'))
        with self.assertRaises(ValueError):
            exact_window([split],dict(windowId='pty-owned',sessionIds=['one','two']),{})

    def test_legacy_observation_preserves_saved_fingerprint(self):
        desktop=Desktop('/tmp');old=replace(desktop.source,window_id='42')
        native=replace(old,window_id='pty-owned')
        observation=Observation((dict(id='pty-owned',aliases=['42']),),(native,),{})
        self.assertEqual(observation.window('42').fingerprint,old.fingerprint)
        self.assertEqual(observation.raw_window('42')['id'],'pty-owned')
        self.assertIsNone(observation.window('other'))


class TransportTests(unittest.TestCase):
    def test_connection_failure_is_not_submitted(self):
        terminal=NativeITerm()
        with patch.object(terminal,'connect',side_effect=RuntimeError('offline')):
            with self.assertRaises(NotSubmitted):terminal.call('create',command='shell')

    def test_lost_reply_is_uncertain_and_never_replayed(self):
        terminal=NativeITerm();terminal.process=Mock()
        with patch.object(terminal,'connect'),patch.object(terminal,'receive',side_effect=EOFError()),patch.object(terminal,'close_connection') as close:
            with self.assertRaises(Uncertain):terminal.call('create',command='shell')
            self.assertEqual(terminal.process.stdin.write.call_count,1)
            close.assert_called_once()

    def test_mismatched_receipt_never_replayed(self):
        terminal=NativeITerm();terminal.process=Mock()
        with patch.object(terminal,'connect'),patch.object(terminal,'receive',return_value=dict(id=999,result={})),patch.object(terminal,'close_connection'):
            with self.assertRaises(Uncertain):terminal.call('close',windowId='owned',sessionIds=['one'])
            self.assertEqual(terminal.process.stdin.write.call_count,1)


class CompanionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.inside=False;owner=self
        class Transaction:
            def __init__(self,connection):pass
            async def __aenter__(self):owner.inside=True
            async def __aexit__(self,*args):owner.inside=False
        self.owned=window();self.app=N(terminal_windows=[self.owned],get_session_by_id=lambda sid:None)
        self.api=N(Transaction=Transaction,async_get_app=AsyncMock(return_value=self.app),
                   LocalWriteOnlyProfile=lambda: N(set_use_custom_command=lambda value:None,set_command=lambda value:None,values={}),
                   rpc=N(async_create_tab=AsyncMock()),
                   api_pb2=N(CreateTabResponse=N(Status=N(Value=lambda value:0))))
        self.companion=TerminalAPI(self.api,object())

    async def test_close_validates_exact_layout_inside_transaction(self):
        async def close(force):
            self.assertTrue(self.inside);self.assertFalse(force)
        self.owned.async_close.side_effect=close
        result=await self.companion.perform(dict(action='close',windowId='pty-owned',sessionIds=['one','two']))
        self.assertEqual(result,dict(acknowledged=True))
        self.assertFalse(self.inside)

    async def test_changed_layout_never_submits_close(self):
        result=await self.companion.perform(dict(action='close',windowId='pty-owned',sessionIds=['one']))
        self.assertIn('notSubmitted',result);self.owned.async_close.assert_not_called()

    async def test_create_releases_transaction_before_async_window_setup(self):
        async def create(*args,**kwargs):
            self.assertFalse(self.inside)
            self.assertEqual(kwargs['window'],'pty-owned')
            return N(create_tab_response=N(status=0,window_id='pty-owned',session_id='new'))
        self.api.rpc.async_create_tab.side_effect=create
        result=await self.companion.perform(dict(action='create',windowId='pty-owned',sessionIds=['one','two'],command='shell'))
        self.assertEqual(result['sessionId'],'new')

    async def test_presentation_failure_retains_creation_receipt(self):
        self.api.rpc.async_create_tab.return_value=N(create_tab_response=N(status=0,window_id='new-window',session_id='new'))
        self.api.async_get_app.side_effect=[self.app,RuntimeError('presentation failed')]
        result=await self.companion.perform(dict(action='create',command='shell',name='title'))
        self.assertEqual(result['sessionId'],'new');self.assertTrue(result['presentationWarnings'])
        self.api.rpc.async_create_tab.assert_awaited_once()

    async def test_create_missing_response_is_uncertain(self):
        self.api.rpc.async_create_tab.side_effect=EOFError('lost')
        result=await self.companion.perform(dict(action='create',command='shell'))
        self.assertIn('uncertain',result)
        self.api.rpc.async_create_tab.assert_awaited_once()

    async def test_appending_to_old_save_keeps_consistent_window_ids(self):
        self.companion.aliases={'pty-owned':'42'}
        self.api.rpc.async_create_tab.return_value=N(create_tab_response=N(status=0,window_id='pty-owned',session_id='new'))
        result=await self.companion.perform(dict(action='create',windowId='42',sessionIds=['one','two'],command='shell'))
        self.assertEqual(result['windowId'],'42')
