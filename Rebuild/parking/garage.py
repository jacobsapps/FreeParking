"""Presentation adapter for the original parking UI; all mutations use v2 workflow."""
import json
from pathlib import Path
import sys
import uuid
from .model import ParkingError, Window
from .service import compose, DEFAULT_ROOT
from .settings import Settings


def tab_view(tab, index, issue=''):
    return dict(index=index, title=tab.title, terminal_id=tab.terminal_id,
                provider=tab.provider, session_id=tab.session_id, cwd=tab.cwd,
                issue=issue, resume_command=None)


def live_windows(snapshot):
    result=[]
    for raw in snapshot.raw:
        window=snapshot.window(raw['id']);tabs=[]
        for raw_tab in raw['tabs']:
            for session in raw_tab['sessions']:
                tab=snapshot.resolved_tabs.get(session['id'])
                if not tab and window:
                    tab=next((t for t in window.tabs if t.terminal_id==session['id']),None)
                issue=snapshot.tab_issues.get(session['id'],'')
                if tab: tabs.append(tab_view(tab,len(tabs),issue))
                else: tabs.append(dict(index=len(tabs),title=session.get('title',''),terminal_id=session['id'],
                    provider='unknown',session_id='',cwd='',issue=issue or snapshot.issues.get(raw['id'],'Identification pending.'),resume_command=None))
        for tab in tabs:
            tab['needs_confirmation'] = tab['terminal_id'] in snapshot.confirmation_candidates
        result.append(dict(id=raw['id'],title=tabs[0]['title'] if tabs else 'Empty window',tabs=tabs,
            can_park=window is not None,fingerprint=window.fingerprint if window else ''))
    return result


def car_view(record, root):
    source=Window.decode(record['source'])
    statuses={'saved':'saved_open','interrupting':'saved_open','closing':'saved_open',
              'parked':'parked','opening':'restoring','checking':'attention','returned':'archived'}
    tabs=[tab_view(t,i) for i,t in enumerate(source.tabs)]
    from .agents import command
    from .providers import Shell
    for saved, rendered in zip(source.tabs, tabs):
        try:
            argv=([str(Path.home()/'.local/bin/claude'),'--resume',saved.session_id] if saved.provider=='claude'
                  else ['codex','resume',saved.session_id])
            rendered['resume_command']=(Shell(None).launch(saved,str(uuid.uuid4())) if saved.provider=='shell'
                else command(saved,str(uuid.uuid4()),argv))
        except ParkingError: pass
    return dict(id=record['id'],created_at=record['created_at'],status=statuses[record['phase']],
        note=record.get('issue') or '',tabs=tabs,
        file_path=str(root/'recovery.sqlite'),window_title=source.tabs[0].title,
        return_verified=record['phase']=='returned',
        restored_targets={k:dict(window_id=v['window_id'],terminal_id=v['terminal_id']) for k,v in record['targets'].items()},
        pending_tab=record['intent'].get('index') if record.get('intent') else None)


def park_all(store, observer, workflow, snapshot=None):
    snapshot=snapshot or observer.observe()
    if snapshot.issues:
        raise ParkingError('Nothing closed. Review the highlighted tabs before parking.')
    windows=list(snapshot.windows)
    if any(t.provider != 'shell' and t.activity == 'unknown' for w in windows for t in w.tabs):
        raise ParkingError('Nothing closed. An agent has not reported its current activity; refresh before parking.')
    # Every window's recovery must be durable before the first interruption.
    for window in windows:
        with store.operation(window.window_id):
            record,receipt=store.begin(window)
            if record['phase']!='saved' or record['intent']:
                raise ParkingError('An earlier parking operation needs checking. Nothing closed.')
            store.verify(receipt)
    results=[]
    for window in windows:
        record=workflow.park(window.window_id,window.fingerprint);results.append(record)
        if record['phase']!='parked':break  # Cancel/uncertainty stops the batch.
    return results


def dispatch(arguments, root=DEFAULT_ROOT):
    args=list(arguments);scope=None;live=False
    if args[:1]==['--live']:live=True;args=args[1:]
    if args[:1]==['--test-scope']:scope=Path(args[1]);args=args[2:]
    store,observer,workflow=compose(root,scope_path=scope,live=live)
    root=store.root
    reply=dict(ok=True,windows=[],cars=[],archives=[],warnings=[],message='',error=None,
               error_code=None,confirmation=None,verified_archived_ids=[],settings=Settings(root).read())
    action=args[0] if args else 'list'
    try:
        if action not in ('list','scan','settings') and not (live or scope):
            raise ParkingError('A live-app or disposable-scope capability is required.')
        if action=='settings':
            if len(args)==2:reply['settings']=Settings(root).save(json.loads(args[1]))
        elif action=='scan':
            snapshot=observer.observe();reply['windows']=live_windows(snapshot)
            # Expose every raw window and affected tab even when resolution fails.
            for record in store.all():
                if record['phase']!='returned':
                    try:workflow.reconcile(record['id'],read_only=True)
                    except Exception as e:reply['warnings'].append(str(e))
            if snapshot.issues:
                reply['message']='\n'.join(
                    '“'+tab['title']+'”: '+tab['issue']
                    for window in reply['windows'] for tab in window['tabs']
                    if tab['issue'] and not tab.get('needs_confirmation'))
        elif action in ('park-all','park','park-confirmed'):
            from .confirmation import prepare, accept
            if action == 'park-confirmed':
                window_id = accept(root,args[1],observer)
                snapshot = observer.observe()
            else:
                window_id = args[1] if action == 'park' else None
                snapshot = observer.observe()
                reply['confirmation'] = prepare(root,snapshot,window_id)
            if reply['confirmation'] is None:
                if window_id is None:
                    results=park_all(store,observer,workflow,snapshot)
                else:
                    window=snapshot.window(window_id)
                    if window is None: raise ParkingError('This window has unidentified tabs. Review its tab details; nothing closed.')
                    fingerprint=window.fingerprint if action=='park-confirmed' else args[2]
                    results=[workflow.park(window_id,fingerprint)]
                if results and results[-1]['phase']!='parked':
                    reply.update(ok=False,error=results[-1].get('issue') or 'Parking is still being checked; recovery is retained.')
                reply['windows']=live_windows(observer.observe())
            else:
                reply['windows']=live_windows(snapshot)
        elif action=='restore':
            record=workflow.reopen(args[1])
            if record['phase']!='returned':reply.update(ok=False,error=record.get('issue') or 'Opening is being checked. Nothing was duplicated.')
        elif action in ('remove','unarchive'):
            # Presentation only: do not pretend native parking state changed.
            record=store.load(args[1])
            with store.operation(record['source']['window_id']):
                record=store.load(args[1]);record['ui_hidden']=action=='remove';store.save(record)
            if action=='unarchive' and record['phase']=='returned':
                reply['message']='This backup already returned. No duplicate window was opened.'
        elif action!='list':raise ParkingError('Unknown garage operation.')
    except Exception as error:
        reply.update(ok=False,error=str(error))
        # Rejected Park All must still offer per-tab diagnostic review.
        if action in ('park-all','park','park-confirmed'):
            try:reply['windows']=live_windows(observer.observe())
            except Exception:pass
    for record in store.all():
        car=car_view(record,root)
        if record['phase']=='returned' or record.get('ui_hidden'):
            reply['archives'].append(car)
            if record['phase']=='returned':reply['verified_archived_ids'].append(record['id'])
        else:reply['cars'].append(car)
    return reply


def main():
    try:reply=dispatch(sys.argv[1:])
    except Exception as e:reply=dict(ok=False,windows=[],cars=[],archives=[],warnings=[],error=str(e))
    print(json.dumps(reply))
