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
        self.threads={sid:dict(id=sid,source='vscode') for sid in self.rows}
        self.rpc=Mock();self.rpc.call.side_effect=self.call
        self.identity=CodexIdentity(self.processes,self.rpc)
    def call(self,method,params):
        if method=='thread/loaded/list':return dict(data=list(self.rows))
        if method=='thread/read':return dict(thread=self.threads[params['threadId']])
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
    def test_unrelated_thread_disappearing_during_inspection_does_not_invalidate_target(self):
        original=self.call;count=0
        def call(method,params):
            nonlocal count
            if method=='thread/loaded/list':
                count+=1
                if count>=2:return dict(data=[self.sid])
            return original(method,params)
        self.rpc.call.side_effect=call
        self.assertEqual(self.identity.resolve(self.pid),self.sid)
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

    def test_kernel_endpoint_and_birth_are_rechecked_at_observation_end(self):
        self.identity.begin_observation()
        self.identity.resolve(self.pid)
        self.processes.loopback_listeners.return_value = {'http://127.0.0.1:9000'}
        self.assertIn(self.pid.pid, self.identity.end_observation())
        self.processes.loopback_listeners.return_value = {'http://127.0.0.1:5001'}
        self.identity.begin_observation()
        self.identity.resolve(self.pid)
        self.processes.all.return_value[50]['started'] = 'reused birth'
        self.assertIn(self.pid.pid, self.identity.end_observation())

    def test_binding_change_at_observation_end_is_detected(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        self.rows[self.other]=self.rows[self.sid]
        self.assertIn(self.pid.pid,self.identity.end_observation())

    def test_unrelated_endpoint_changes_do_not_invalidate_target(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        self.rows[self.other]=('http://127.0.0.1:6002','connected')
        self.assertEqual(self.identity.end_observation(),{})

    def test_new_unrelated_thread_does_not_invalidate_target(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        new=str(uuid.uuid4());self.rows[new]=('http://127.0.0.1:6003','connected')
        self.threads[new]=dict(id=new,source='cli')
        self.assertEqual(self.identity.end_observation(),{})

    def test_new_competing_owner_invalidates_target(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        new=str(uuid.uuid4());self.rows[new]=self.rows[self.sid]
        self.threads[new]=dict(id=new,source='cli')
        self.assertIn(self.pid.pid,self.identity.end_observation())

    def test_new_child_with_same_owner_does_not_invalidate_target(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        new=str(uuid.uuid4());self.rows[new]=self.rows[self.sid]
        self.threads[new]=dict(id=new,source={'subAgent':{'thread_spawn':{'parent_thread_id':self.sid}}})
        self.assertEqual(self.identity.end_observation(),{})

    def test_target_endpoint_change_invalidates_target(self):
        self.identity.begin_observation();self.identity.resolve(self.pid)
        self.rows[self.sid]=('http://127.0.0.1:6001','connected')
        self.assertIn(self.pid.pid,self.identity.end_observation())

    def test_missing_endpoint_fallback_is_invalidated_if_native_owner_appears(self):
        self.rows[self.sid]=(None,'connected')
        self.identity.begin_observation()
        with self.assertRaises(ParkingError): self.identity.resolve(self.pid)
        self.rows[self.sid]=('http://127.0.0.1:5001','connected')
        self.assertIn(self.pid.pid,self.identity.end_observation())

    def test_target_ancestry_change_invalidates_target_even_when_endpoints_stable(self):
        self.rows[self.other]=self.rows[self.sid]
        self.threads[self.other]['source']={'subAgent':{'thread_spawn':{'parent_thread_id':self.sid}}}
        self.identity.begin_observation();self.identity.resolve(self.pid)
        self.threads[self.other]['source']='cli'
        self.assertIn(self.pid.pid,self.identity.end_observation())

    def test_competitor_added_mid_inventory_is_inspected(self):
        original=self.call;count=0;new=str(uuid.uuid4())
        def call(method,params):
            nonlocal count
            if method=='thread/loaded/list':
                count+=1
                if count==2:
                    self.rows[new]=self.rows[self.sid]
                    self.threads[new]=dict(id=new,source='cli')
            return original(method,params)
        self.rpc.call.side_effect=call
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)

    def test_observation_binding_failure_is_scoped_to_process(self):
        other_process=Process(51,'other birth','/dev/other','/bin/codex')
        self.processes.all.return_value[51]=dict(started='other birth',tty='/dev/other')
        self.processes.loopback_listeners.side_effect=lambda pid: {'http://127.0.0.1:5001' if pid==50 else 'http://127.0.0.1:5002'}
        self.identity.begin_observation()
        self.assertEqual(self.identity.resolve(self.pid),self.sid)
        self.assertEqual(self.identity.resolve(other_process),self.other)
        self.rows[self.other]=('http://127.0.0.1:6002','connected')
        self.assertEqual(set(self.identity.end_observation()),{51})

    def test_settle_retries_a_read_race_without_repeating_any_mutation(self):
        from parking.verifier import Observer
        from unittest.mock import patch
        observer=Observer(None,None,None)
        observer.observe=Mock(side_effect=[Uncertain('startup changed'), 'stable'])
        with patch('parking.verifier.time.sleep'):
            self.assertEqual(observer.observe_until(lambda s:s=='stable'),'stable')
        self.assertEqual(observer.observe.call_count,2)

    def test_native_child_agents_share_parent_endpoint_without_ambiguity(self):
        self.rows[self.other]=self.rows[self.sid]
        self.threads[self.other]['source']={'subAgent':{'thread_spawn':{'parent_thread_id':self.sid}}}
        self.assertEqual(self.identity.resolve(self.pid),self.sid)

    def test_nested_subagents_resolve_to_one_native_root(self):
        child=str(uuid.uuid4());self.rows[child]=self.rows[self.sid];self.rows[self.other]=self.rows[self.sid]
        self.threads[self.other]['source']={'subAgent':{'thread_spawn':{'parent_thread_id':self.sid}}}
        self.threads[child]=dict(id=child,source={'subAgent':{'thread_spawn':{'parent_thread_id':self.other}}})
        self.assertEqual(self.identity.resolve(self.pid),self.sid)

    def test_unrelated_or_cyclic_child_ancestry_is_not_guessed(self):
        self.rows[self.other]=self.rows[self.sid]
        for parent in (str(uuid.uuid4()),self.other):
            self.threads[self.other]['source']={'subAgent':{'thread_spawn':{'parent_thread_id':parent}}}
            with self.assertRaises(ParkingError):self.identity.resolve(self.pid)

    def test_fork_metadata_is_not_subagent_ownership(self):
        self.rows[self.other]=self.rows[self.sid]
        self.threads[self.other]['forkedFromId']=self.sid
        with self.assertRaises(ParkingError):self.identity.resolve(self.pid)

    def test_malformed_subagent_metadata_is_rejected(self):
        self.rows[self.other]=self.rows[self.sid]
        for value in (None, 'worker', {}, {'thread_spawn':None}, {'thread_spawn':'worker'}):
            self.threads[self.other]['source']={'subAgent':value}
            with self.assertRaises(ParkingError):self.identity.resolve(self.pid)
