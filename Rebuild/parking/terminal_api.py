# SPDX-License-Identifier: GPL-2.0-or-later
"""Standalone persistent iTerm API companion; JSON requests, exact native targets.

The recovery worker owns all durable intents. This companion never types text,
interrupts agents, changes preferences, or retries a submitted mutation.
"""
import asyncio
import json
from pathlib import Path
import sys

vendor=Path(__file__).resolve().parents[1]/'vendor'
if not vendor.is_dir():vendor=Path(__file__).resolve().parents[2]/'.build/iterm-api'


def groups(window):
    return [[s.session_id for s in t.sessions] for t in window.tabs]


def frame_bounds(bounds):
    if bounds.get('coordinate_system') == 'iterm-api': return bounds
    # AppleScript measures down from the main screen's top; Cocoa measures up
    # from its bottom. Preserve older saved positions on any attached display.
    import ctypes
    path=Path(__file__).resolve().parents[1]/'libparking_process.dylib'
    if not path.exists():path=Path(__file__).resolve().parents[2]/'.build/libparking_process.dylib'
    library=ctypes.CDLL(str(path))
    library.fp_main_screen_height.restype=ctypes.c_double
    return dict(bounds,y=library.fp_main_screen_height()-bounds['y']-bounds['height'])


def exact_window(windows, request, aliases):
    wid=request['windowId']
    matches=[w for w in windows if w.window_id==wid or aliases.get(w.window_id)==wid]
    if len(matches)!=1:raise ValueError('The exact saved window is no longer present.')
    window=matches[0];layout=groups(window)
    if any(len(g)!=1 for g in layout) or [g[0] for g in layout]!=request['sessionIds']:
        raise ValueError('The exact tab layout changed. No window operation was performed.')
    return window


class TerminalAPI:
    def __init__(self, api, connection):
        self.api,self.connection=api,connection
        self.aliases={}

    async def variables(self, session, names):
        result=await self.api.rpc.async_variable(self.connection,session_id=session,sets=[],gets=names)
        response=result.variable_response
        if response.status!=self.api.api_pb2.VariableResponse.Status.Value('OK') or len(response.values)!=len(names):
            raise ValueError('iTerm returned incomplete session variables.')
        return dict(zip(names,[json.loads(value) for value in response.values]))

    async def inventory(self, app, metadata):
        async def session_row(session, title):
            names=['tty','user.freeParkingReceipt']+(['autoName'] if metadata else [])
            values=await self.variables(session.session_id,names)
            return dict(id=session.session_id,tty=values.get('tty') or '',title=title or session.name if metadata else '',
                        name=values.get('autoName') or '',receipt=values.get('user.freeParkingReceipt') or '')
        async def tab_row(tab):
            title=await tab.async_get_variable('title') if metadata else ''
            return dict(sessions=await asyncio.gather(*(session_row(s,title) for s in tab.sessions)))
        async def window_row(window):
            frame=window.frame
            bounds=dict(x=frame.origin.x,y=frame.origin.y,width=frame.size.width,height=frame.size.height,
                        coordinate_system='iterm-api') if metadata else {}
            return dict(id=self.aliases.get(window.window_id,window.window_id),
                        aliases=[window.window_id] if window.window_id in self.aliases else [],
                        bounds=bounds,tabs=await asyncio.gather(*(tab_row(t) for t in window.tabs)))
        return await asyncio.gather(*(window_row(w) for w in app.terminal_windows))

    async def perform(self, request):
        submitted=False
        try:
            async with self.api.Transaction(self.connection):
                app=await self.api.async_get_app(self.connection)
                if request.get('legacy'):
                    for native in app.terminal_windows:
                        matches=[w for w in request['legacy'] if
                            [[s['id'] for s in t['sessions']] for t in w['tabs']]==groups(native)]
                        if len(matches)==1:self.aliases[native.window_id]=matches[0]['id']
                action=request['action']
                if action=='list':return await self.inventory(app,request.get('metadata',True))
                if action not in ('create','close','reorder'):raise ValueError('Unknown terminal operation.')
                window=exact_window(app.terminal_windows,request,self.aliases) if request.get('windowId') is not None else None
                if action=='close':
                    if window is None:raise ValueError('Closing requires an exact window.')
                    submitted=True
                    await window.async_close(force=False)
                    return dict(acknowledged=True)
                if action=='reorder':
                    if window is None:raise ValueError('Reordering requires an exact window.')
                    expected=request['sessionIds'];desired=request['desiredIds']
                    if len(set(expected))!=len(expected) or sorted(expected)!=sorted(desired):raise ValueError('Invalid exact tab reorder.')
                    tabs=[window.tabs[expected.index(sid)] for sid in desired]
                    submitted=True
                    await window.async_set_tabs(tabs)
                    return dict(acknowledged=True)
                if not isinstance(request.get('command'),str) or not request['command']:raise ValueError('Missing launch specification.')
                profile=self.api.LocalWriteOnlyProfile()
                profile.set_use_custom_command('Yes');profile.set_command(request['command'])
            # Creation waits for asynchronous window setup. Holding iTerm's
            # transaction here deadlocks that setup. The request still uses
            # the exact checked UUID, never a current/frontmost window.
            submitted=True
            result=await self.api.rpc.async_create_tab(self.connection,window=window.window_id if window else None,
                                                     profile_customizations=profile.values)
            created=result.create_tab_response
            if created.status!=self.api.api_pb2.CreateTabResponse.Status.Value('OK'):
                raise ValueError('iTerm did not acknowledge creation: '+str(created.status))
            response=dict(windowId=self.aliases.get(created.window_id,created.window_id),
                          sessionId=created.session_id,presentationWarnings=[])
            # Presentation runs after the transaction and after capturing the
            # creation receipt. Its failure can never create a duplicate tab.
            try:
                app=await self.api.async_get_app(self.connection)
                session=app.get_session_by_id(created.session_id)
                if session and (request.get('name') or request.get('title')):
                    await session.async_set_name(request.get('name') or request['title'])
                bounds=request.get('bounds') or {}
                if bounds:
                    bounds=frame_bounds(bounds)
                    window=app.get_window_by_id(created.window_id)
                    frame=self.api.util.Frame(self.api.util.Point(bounds['x'],bounds['y']),self.api.util.Size(bounds['width'],bounds['height']))
                    await window.async_set_frame(frame)
            except Exception as error:response['presentationWarnings'].append('Window presentation: '+str(error))
            return response
        except Exception as error:
            return {'uncertain' if submitted else 'notSubmitted':str(error)}


async def serve(connection,api):
    terminal=TerminalAPI(api,connection)
    print(json.dumps(dict(ready=True)),flush=True)
    loop=asyncio.get_running_loop()
    while True:
        line=await loop.run_in_executor(None,sys.stdin.readline)
        if not line:return
        request=json.loads(line)
        result=await terminal.perform(request)
        print(json.dumps(dict(id=request['id'],result=result)),flush=True)


def main():
    sys.path.insert(0,str(vendor))
    import iterm2
    iterm2.run_until_complete(lambda connection:serve(connection,iterm2))


if __name__=='__main__':main()
