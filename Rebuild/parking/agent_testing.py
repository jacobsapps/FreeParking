"""Creation of new, owned agent conversations for the disposable lab only."""
from pathlib import Path
import subprocess
import time
import uuid
from .adapters import ITerm
from .codex_rpc import CodexRPC
from .codex_testing import OwnedCodex
from .model import ParkingError
from .store import atomic_json

SEED = ('Reply exactly READY. This is a disposable Free Parking test. '
        'If a later user message is exactly "please continue", reply exactly CONTINUED and stop. '
        'Never use tools or edit files in this test.')
BUSY = ('For this disposable interruption test, count from 1 to 10000, one number per line. '
        'Do not use tools or files. The earlier rule for "please continue" still applies.')

class AgentSeeder:
    def __init__(self, scope): self.scope=scope

    def seed(self, provider, folder, busy=False):
        root=self.scope.path.parent
        if root.resolve() not in Path(folder).resolve().parents:
            raise ParkingError('Agent fixture folder escaped its scope.')
        self.scope.audit(ITerm().windows())
        if provider == 'claude':
            sid=str(uuid.uuid4());intent=root/'seed-intents'/(sid+'.json')
            atomic_json(intent,dict(provider=provider,session_id=sid,cwd=folder,message=SEED))
            result=subprocess.run([str(Path.home()/'.local/bin/claude'),'-p',SEED,
                '--session-id',sid,'--max-turns','1','--tools',''],cwd=folder,
                stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120)
            atomic_json(root/'seed-results'/(sid+'.json'),dict(exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
            if result.returncode or result.stdout.strip()!='READY':
                raise ParkingError('Claude fixture seeding failed; no terminal launched.')
        elif provider == 'codex':
            rpc=CodexRPC();owned=OwnedCodex(root/'owned-codex-threads.json',rpc)
            with rpc.connected():
                sid=owned.create(folder)['id'];owned.start_turn(sid,SEED)
                deadline=time.monotonic()+90;complete=False
                while time.monotonic()<deadline:
                    time.sleep(.3)
                    try: thread=rpc.read(sid)
                    except ParkingError: continue  # Observe only: never resubmit a seed.
                    if thread['turns'] and thread['turns'][-1]['status']=='completed':complete=True;break
                if not complete:raise ParkingError('Codex seed turn has not completed. No duplicate turn started.')
                owned.permit(sid)
                rpc.call('thread/name/set',dict(threadId=sid,name='Free Parking disposable fixture'))
        else: raise ParkingError('Unknown disposable agent provider.')
        self.scope.audit(ITerm().windows())
        result=dict(provider=provider,session_id=sid,cwd=folder)
        if busy:result['initial_prompt']=BUSY
        return result
