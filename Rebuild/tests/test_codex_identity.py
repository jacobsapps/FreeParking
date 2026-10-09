import sys,uuid,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).parents[1]))
from parking.codex_identity import CodexIdentity,loopback_origin
from parking.model import Process,ParkingError,Uncertain

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.sid=str(uuid.uuid4());self.other=str(uuid.uuid4())
        self.pid=Process(50,'birth','/dev/own','/bin/codex')
        self.processes=Mock();self.processes.loopback_listeners.return_value={'http://127.0.0.1:5001'}
        self.processes.all.return_value={50:dict(started='birth',tty='/dev/own')}
        self.rows={self.sid:('http://127.0.0.1:5001','connected'),self.other:('http://127.0.0.1:5002','connected')}
        self.rpc=Mock();self.rpc.call.side_effect=self.call
        self.identity=CodexIdentity(self.processes,self.rpc)
    def call(self,method,params):
        if method=='thread/loaded/list':return dict(data=list(self.rows))
        self.assertEqual(method,'mcpServerStatus/list');self.assertEqual(params['serverName'],'codex_tui')
        origin,status=self.rows[params['threadId']]
        return dict(data=[dict(name='codex_tui',httpOrigin=origin,runtimeStatus=status)])
    def test_exact_endpoint_identifies_thread_without_any_title_or_folder(self):
        self.assertEqual(self.identity.resolve(self.pid),self.sid)
    def test_two_conversations_on_one_tui_are_ambiguous(self):
        self.rows[self.other]=self.rows[self.sid]
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)
    def test_missing_endpoint_is_not_guessed(self):
        self.rows[self.sid]=(None,'connected')
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)
    def test_disconnected_endpoint_is_not_identity(self):
        self.rows[self.sid]=('http://127.0.0.1:5001','failed')
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)
    def test_reused_pid_is_rejected(self):
        self.processes.all.return_value[50]['started']='new birth'
        with self.assertRaises(Uncertain):self.identity.resolve(self.pid)
    def test_port_changes_during_inspection_are_rejected(self):
        self.processes.loopback_listeners.side_effect=[{'http://127.0.0.1:5001'},{'http://127.0.0.1:6001'}]
        with self.assertRaises(Uncertain):self.identity.resolve(self.pid)
    def test_thread_set_changes_during_inspection_are_rejected(self):
        original=self.call;count=0
        def call(method,params):
            nonlocal count
            if method=='thread/loaded/list':
                count+=1
                if count==2:return dict(data=[self.sid])
            return original(method,params)
        self.rpc.call.side_effect=call
        with self.assertRaises(Uncertain):self.identity.resolve(self.pid)
    def test_incomplete_inventory_is_rejected(self):
        self.rpc.call.side_effect=lambda m,p:dict(data=[self.sid]) if m=='thread/loaded/list' else dict(data=[],nextCursor='more')
        with self.assertRaises(Uncertain):self.identity.resolve(self.pid)
    def test_only_literal_loopback_origin_is_accepted(self):
        for value in ('http://evil.example:5001','http://127.0.0.1:5001/path','http://user@127.0.0.1:5001','http://127.0.0.1:5001?x','https://127.0.0.1:5001','http://127.0.0.1:99999',None):
            self.assertIsNone(loopback_origin(value))
        self.assertEqual(loopback_origin('http://127.0.0.1:5001'),'http://127.0.0.1:5001')

    def test_one_binding_snapshot_is_shared_only_within_an_observation(self):
        self.identity.begin_observation()
        self.assertEqual(self.identity.resolve(self.pid),self.sid)
        before=self.rpc.call.call_count
        self.assertEqual(self.identity.resolve(self.pid),self.sid)
        self.assertEqual(self.rpc.call.call_count,before)
        self.identity.end_observation()
        self.rows[self.sid]=('http://127.0.0.1:7000','connected')
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)

    def test_binding_change_at_observation_end_is_detected(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        self.rows[self.other]=self.rows[self.sid]
        with self.assertRaises(Uncertain):self.identity.end_observation()

    def test_settle_retries_a_read_race_without_repeating_any_mutation(self):
        from parking.verifier import Observer
        from unittest.mock import patch
        observer=Observer(None,None,None)
        observer.observe=Mock(side_effect=[Uncertain('startup changed'), 'stable'])
        with patch('parking.verifier.time.sleep'):
            self.assertEqual(observer.observe_until(lambda s:s=='stable'),'stable')
        self.assertEqual(observer.observe.call_count,2)
