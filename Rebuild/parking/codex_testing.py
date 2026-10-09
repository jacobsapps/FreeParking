"""Persistent ownership boundary for isolated Codex protocol component tests."""
import uuid
from pathlib import Path
from .model import ParkingError
from .store import atomic_json


class OwnedCodex:
    def __init__(self, path, rpc):
        self.path=Path(path);self.rpc=rpc
        if self.path.exists():
            import json
            self.data=json.loads(self.path.read_text())
        else:
            self.data=dict(protected=rpc.call('thread/loaded/list',{})['data'],owned={},pending=None)
            self.save()

    def save(self):atomic_json(self.path,self.data)

    def create(self,folder):
        folder=Path(folder).resolve()
        if self.path.parent.resolve() not in folder.parents:raise ParkingError('Fixture folder must be isolated.')
        if self.data['pending']:raise ParkingError('Earlier create is unconfirmed; never replay it.')
        self.data['pending']=dict(cwd=str(folder));self.save()
        result=self.rpc.call('thread/start',dict(cwd=str(folder),ephemeral=False))
        thread=result['thread'];sid=thread['id'];uuid.UUID(sid)
        if sid in self.data['protected'] or sid in self.data['owned'] or thread['cwd']!=str(folder):
            raise ParkingError('Codex create receipt did not establish a new isolated thread.')
        self.data['owned'][sid]=dict(cwd=str(folder),events=[])
        self.data['pending']=None;self.save()
        return thread

    def permit(self,sid,turn=None):
        if sid in self.data['protected'] or sid not in self.data['owned'] or self.data['pending']:
            raise ParkingError('Only this tester’s freshly created Codex thread may be changed.')
        if turn is not None:
            if not any(e.get('turn_id')==turn and e['status']=='acknowledged' for e in self.data['owned'][sid]['events']):
                raise ParkingError('This tester never received ownership of that turn.')
            return  # Exact server-returned thread + turn pair; never selects a newer turn.
        current=self.rpc.call('thread/read',dict(threadId=sid,includeTurns=False))['thread']
        if current['cwd']!=self.data['owned'][sid]['cwd']:raise ParkingError('Test thread changed folder.')

    def start_turn(self,sid,text):
        self.permit(sid)
        event=dict(action='turn/start',message=text,status='submitted')
        self.data['owned'][sid]['events'].append(event);self.save()
        result=self.rpc.call('turn/start',dict(threadId=sid,input=[dict(type='text',text=text,text_elements=[])]))
        event.update(status='acknowledged',turn_id=result['turn']['id']);self.save()
        return result['turn']
