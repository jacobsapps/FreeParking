"""One completion contract for the app and disposable acceptance harness.

Issue a requested operation once, then observe only its durable record. Checking
never replays writes or resumes a partial launch loop. Only the original park
request can finish its already-authorized close sequence. A bounded check can end pending; that is not proof of failure.
"""
from dataclasses import dataclass
import time
from .model import Uncertain


@dataclass(frozen=True)
class Outcome:
    record: dict
    status: str  # completed, checking, needs_action
    detail: str = ''


def pending(record):
    """Only outcomes which read-only reconciliation can still establish."""
    if record['phase'] in ('parked', 'returned', 'saved'):
        return False
    if finish_parking_available(record): return False
    if record.get('intent'):
        return True
    if record.get('issue_kind') in ('not_submitted', 'failed'): return False
    return (record['phase'] in ('opening', 'checking') and bool(record['source']['tabs'])
            and len(record['targets']) == len(record['source']['tabs']))


def finish_parking_available(record):
    return (record['phase'] == 'closing' and record.get('close_confirmed') is True
            and record.get('intent') == {'kind': 'close'})


class Operations:
    def __init__(self, workflow, seconds=30, clock=time.monotonic, sleep=time.sleep):
        self.workflow = workflow
        self.seconds, self.clock, self.sleep = seconds, clock, sleep

    def park(self, window_id, fingerprint):
        return self.finish(self.workflow.park(window_id, fingerprint), 'parked', finish_closing=True)

    def reopen(self, record_id):
        try:
            record = self.workflow.reopen(record_id)
        except Exception as error:
            # Reopening may have created tabs before a later read failed. Keep
            # that error with the durable operation instead of losing it in UI.
            record = self.workflow.store.load(record_id)
            with self.workflow.store.operation(record['source']['window_id']):
                record = self.workflow.store.load(record_id)
                if not record.get('issue'): self.workflow.note(record, error)
        return self.finish(record, 'returned')

    def finish_parking(self, record_id):
        return self.finish(self.workflow.finish_parking(record_id), 'parked', finish_closing=True)

    def check(self, record_id):
        record = self.workflow.store.load(record_id)
        goal = 'parked' if record['phase'] in ('closing', 'interrupting', 'parked') else 'returned'
        return self.finish(record, goal)

    def finish(self, record, goal, finish_closing=False):
        deadline = self.clock() + self.seconds
        observation_error = ''
        while True:
            if record['phase'] == goal:
                return Outcome(record, 'completed')
            if finish_parking_available(record) and not finish_closing:
                return Outcome(record, 'needs_action', 'The window has closed. Choose Finish parking to complete its saved-session preparation; no tabs will reopen.')
            if not pending(record) and not (finish_closing and finish_parking_available(record)):
                return Outcome(record, 'needs_action', record.get('issue') or
                    'The operation stopped. Recovery is saved; review the tabs before retrying.')
            if self.clock() >= deadline:
                detail = record.get('issue') or observation_error or (
                    'The result is not confirmed yet. Recovery is saved. Check status; nothing will be repeated.')
                return Outcome(record, 'checking', detail)
            try:
                # Never call reopen twice. Only the original park request may
                # finish its authorized close sequence; check/reopen never do.
                record = self.workflow.reconcile(record['id'], read_only=not finish_closing)
                observation_error = ''
            except Uncertain as error:
                observation_error = str(error)
            except Exception as error:
                return Outcome(record, 'needs_action', record.get('issue') or str(error))
            self.sleep(min(0.2, max(0, deadline - self.clock())))
