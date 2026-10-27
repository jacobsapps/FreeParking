"""Presentation adapter for the original parking UI; all mutations use v2 workflow."""
from .diagnostics import timed, event

import json
from pathlib import Path
import sys
import uuid
from .model import ParkingError, Window
from .service import compose, DEFAULT_ROOT
from .settings import Settings
from .operations import Operations, pending, finish_parking_available
from .verifier import Verifier
from .store import atomic_json


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
        can_park = window is not None
        if window:
            for saved, rendered in zip(window.tabs, tabs):
                try:
                    if Verifier.other_conversation_tabs(saved, snapshot, {saved.terminal_id}):
                        raise ParkingError('This conversation is also open in another tab.')
                except ParkingError as error:
                    can_park = False
                    rendered['issue'] = str(error)
        result.append(dict(id=raw['id'],title=tabs[0]['title'] if tabs else 'Empty window',tabs=tabs,
            can_park=can_park,fingerprint=window.fingerprint if window else ''))
    return result


def car_view(record, root):
    source=Window.decode(record['source'])
    statuses={'saved':'saved_open','interrupting':'saved_open','closing':'saved_open',
              'parked':'parked','opening':'restoring','checking':'attention','returned':'archived'}
    if pending(record): statuses[record['phase']] = 'checking'
    if finish_parking_available(record): statuses[record['phase']] = 'finish_parking'
    tabs=[tab_view(t,i) for i,t in enumerate(source.tabs)]
    from .agents import command
    from .providers import Shell
    for saved, rendered in zip(source.tabs, tabs):
        index = str(rendered['index'])
        rendered['restore_state'] = ('opened' if index in record['targets'] else
            'checking' if (record.get('intent') or {}).get('index') == rendered['index'] else 'saved')
        try:
            from .permissions import launch_flags, label
            rendered['permission_label'] = label(saved)
            argv=(['claude',*launch_flags(saved),'--resume',saved.session_id] if saved.provider=='claude'
                  else ['codex',*launch_flags(saved),'resume',saved.session_id])
            rendered['resume_command']=(Shell(None).launch(saved,str(uuid.uuid4())) if saved.provider=='shell'
                else command(saved,str(uuid.uuid4()),argv))
        except ParkingError: pass
    return dict(id=record['id'],created_at=record['created_at'],status=statuses[record['phase']],
        note=record.get('issue') or '\n'.join(record.get('presentation_warnings', [])),tabs=tabs,
        file_path=str(root/'recovery.sqlite'),window_title=source.tabs[0].title,
        return_verified=record['phase']=='returned',
        restored_targets={k:dict(window_id=v['window_id'],terminal_id=v['terminal_id']) for k,v in record['targets'].items()},
        pending_tab=record['intent'].get('index') if record.get('intent') else None)


@timed('garage.park_all')
def park_all(store, observer, workflow, snapshot=None, operations=None, outcomes=None):
    operations = operations or Operations(workflow)
    snapshot=snapshot or observer.observe()
    if snapshot.issues:
        raise ParkingError('Nothing closed. Review the highlighted tabs before parking.')
    windows=list(snapshot.windows)
    if any(t.provider == 'codex' and t.activity == 'unknown' for w in windows for t in w.tabs):
        raise ParkingError('Nothing closed. An agent has not reported its current activity; refresh before parking.')
    for window in windows:
        for tab in window.tabs:
            if Verifier.other_conversation_tabs(tab, snapshot, {tab.terminal_id}):
                raise ParkingError('Nothing closed. A conversation is open in multiple tabs.')
            provider = workflow.gate.providers.get(tab.provider)
            provider.launch(tab, str(uuid.uuid4()))
            preflight = getattr(provider, 'preflight', None)
            if preflight: preflight(tab)
    # Every window's recovery must be durable before the first interruption.
    batch = dict(id=str(uuid.uuid4()), records=[], outcomes={})
    for window in windows:
        with store.operation(window.window_id):
            record,receipt=store.begin(window)
            if record['phase']!='saved' or record['intent']:
                raise ParkingError('An earlier parking operation needs checking. Nothing closed.')
            store.verify(receipt)
            batch['records'].append(record['id'])
            record.update(batch_id=batch['id'], batch_index=len(batch['records']), batch_total=len(windows)); store.save(record)
    batch_path = store.root/'batches'/(batch['id']+'.json')
    atomic_json(batch_path, batch)
    results=[]
    for window in windows:
        outcome=operations.park(window.window_id,window.fingerprint)
        if outcomes is not None:outcomes.append(outcome)
        record=outcome.record;results.append(record)
        batch['outcomes'][record['id']] = dict(status=outcome.status, phase=record['phase'], detail=outcome.detail)
        atomic_json(batch_path, batch)
        if record['phase']!='parked':break  # Cancel/uncertainty stops the batch.
    return results


@timed('garage.dispatch')
def dispatch(arguments, root=DEFAULT_ROOT, progress=None):
    args=[a for a in arguments if a != '--progress'];scope=None;live=False
    if args[:1]==['--live']:live=True;args=args[1:]
    if args[:1]==['--test-scope']:scope=Path(args[1]);args=args[2:]
    store,observer,workflow=compose(root,scope_path=scope,live=live)
    root=store.root
    store.progress = progress
    operations=Operations(workflow)
    reply=dict(ok=True,windows=[],cars=[],archives=[],warnings=[],message='',error=None,
               error_code=None,operation_status=None,operation_id=None,confirmation=None,verified_archived_ids=[],settings=Settings(root).read_optional())
    if scope is None and root == DEFAULT_ROOT:
        from .legacy import import_parked
        reply['warnings'].extend(import_parked(store, Path.home()/'Library/Application Support/Car Park'))
    action=args[0] if args else 'list'
    try:
        if action not in ('list','scan','settings') and not (live or scope):
            raise ParkingError('A live-app or disposable-scope capability is required.')
        if action=='settings':
            if len(args)==2:reply['settings']=Settings(root).save(json.loads(args[1]))
            else:reply['settings']=Settings(root).read()
        elif action=='scan':
            # Existing recoveries can appear before native inspection finishes.
            if progress:
                for record in store.readable()[0]:
                    if record['phase'] != 'returned' and not record.get('ui_hidden'): progress(record)
            snapshot=observer.observe();reply['windows']=live_windows(snapshot)
            # Expose every raw window and affected tab even when resolution fails.
            for record in store.readable()[0]:
                if record['phase']!='returned':
                    try:workflow.reconcile(record['id'],read_only=True,snapshot=snapshot)
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
                outcomes=[]
                if window_id is None:
                    results=park_all(store,observer,workflow,snapshot,operations,outcomes)
                else:
                    window=snapshot.window(window_id)
                    if window is None: raise ParkingError('This window has unidentified tabs. Review its tab details; nothing closed.')
                    fingerprint=window.fingerprint if action=='park-confirmed' else args[2]
                    outcome=operations.park(window_id,fingerprint)
                    results=[outcome.record];outcomes=[outcome]
                if results:
                    last=results[-1]
                    status=outcomes[-1].status
                    reply['operation_status']=status
                    reply['operation_id']=last['id']
                    if status=='needs_action':
                        reply.update(ok=False,error=outcomes[-1].detail)
                    elif status=='checking':
                        reply['message']='Parking is awaiting confirmation. Recovery is saved; check the saved window’s status.'
                    if window_id is None:
                        parked = sum(r['phase']=='parked' for r in results)
                        reply['message'] = str(parked)+' of '+str(len(snapshot.windows))+' windows parked. '+(
                            'Every tab is saved.' if parked == len(snapshot.windows) else 'Remaining windows are saved and still available; choose Finish parking on each remaining window.')
                        reply['batch_id'] = last.get('batch_id')
                try:reply['windows']=live_windows(observer.observe())
                except Exception as error:reply['warnings'].append('Window list refresh did not complete: '+str(error))
            else:
                reply['windows']=live_windows(snapshot)
        elif action in ('restore','check','finish-parking'):
            saved = store.load(args[1])
            outcome=(operations.park(saved['source']['window_id'], saved['fingerprint'])
                     if action=='finish-parking' and saved['phase']=='saved' and not saved['intent']
                     and not Verifier.source_absent(saved, observer.observe()) else
                     operations.reopen(args[1]) if action=='restore' else
                     operations.finish_parking(args[1]) if action=='finish-parking' else
                     Operations(workflow, seconds=5).check(args[1]))
            reply['operation_id']=outcome.record['id']
            reply['operation_status']=outcome.status
            if finish_parking_available(outcome.record):reply['message']=outcome.detail
            elif outcome.status=='needs_action':reply.update(ok=False,error=outcome.detail)
            elif outcome.status=='checking':reply['message']=outcome.detail
            reply['warnings'].extend(outcome.record.get('presentation_warnings', []))
            try:reply['windows']=live_windows(observer.observe())
            except Exception as error:reply['warnings'].append('Window list refresh did not complete: '+str(error))
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
    records, errors = store.readable()
    reply['warnings'].extend(errors)
    for record in records:
        car=car_view(record,root)
        if record['phase']=='returned' or record.get('ui_hidden'):
            reply['archives'].append(car)
            if record['phase']=='returned':reply['verified_archived_ids'].append(record['id'])
        else:reply['cars'].append(car)
    return reply


def main():
    progress_root = DEFAULT_ROOT
    if '--test-scope' in sys.argv:
        progress_root = Path(sys.argv[sys.argv.index('--test-scope') + 1]).resolve().parent/'rebuild-garage'
    def progress(record):
        phase = record['phase']; total = len(record['source']['tabs'])
        index = (record.get('intent') or {}).get('index')
        count = len(record['targets'])
        message = {'saved':'Recovery saved', 'interrupting':'Stopping active task', 'closing':'Closing saved window',
                   'parked':'Window parked', 'opening':'Opening tab '+str((index or 0)+1)+' of '+str(total),
                   'checking':str(count)+' of '+str(total)+' tabs opened; checking sessions', 'returned':'All '+str(total)+' tabs restored'}[phase]
        if (record.get('intent') or {}).get('kind') == 'reorder': message = 'Putting all '+str(total)+' tabs back in their saved order'
        if phase in ('saved','interrupting','closing','parked') and record.get('batch_total'):
            message = 'Window '+str(record['batch_index'])+' of '+str(record['batch_total'])+': '+message
        try: print(json.dumps(dict(progress=message, record_id=record['id'], phase=phase, car=car_view(record, progress_root))), flush=True)
        except BrokenPipeError: pass  # Closing the UI must not interrupt durable recovery.
    from .diagnostics import Trace
    action = next((a for a in sys.argv[1:] if a in ('list','scan','park','park-all','park-confirmed','restore','check','finish-parking','settings','remove','unarchive')), 'unknown')
    with Trace(DEFAULT_ROOT.parent/'Diagnostics', action) as trace:
        try:reply=dispatch(sys.argv[1:], progress=progress if '--progress' in sys.argv else None)
        except Exception as e:reply=dict(ok=False,windows=[],cars=[],archives=[],warnings=[],message='',
                                       error=str(e),error_code=None,verified_archived_ids=[])
        event('operation.result', ok=reply['ok'], cars=len(reply['cars']), windows=len(reply['windows']), status=reply.get('operation_status'))
        reply['diagnostics_path'] = str(trace.path) if trace.path else None
        print(json.dumps(reply))
