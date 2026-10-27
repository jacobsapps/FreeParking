"""Per-tab launch permissions. Missing evidence never inherits a global YOLO default."""

CODEX_SANDBOXES = {'read-only', 'workspace-write', 'danger-full-access'}
CODEX_APPROVALS = {'on-request', 'never'}
CLAUDE_MODES = {'manual', 'plan', 'acceptEdits', 'auto', 'dontAsk', 'bypassPermissions'}


def codex_permissions(value):
    if (isinstance(value, dict) and isinstance(value.get('sandbox'), str)
            and isinstance(value.get('approval'), str) and value['sandbox'] in CODEX_SANDBOXES
            and value['approval'] in CODEX_APPROVALS):
        return dict(sandbox=value['sandbox'], approval=value['approval'])
    return dict(sandbox='read-only', approval='on-request')


def claude_permissions(value):
    mode = value.get('mode') if isinstance(value, dict) else None
    return dict(mode=mode) if isinstance(mode, str) and mode in CLAUDE_MODES else dict(mode='plan')


def launch_flags(tab):
    if tab.provider == 'codex':
        value = codex_permissions(tab.permissions)
        return ['--sandbox', value['sandbox'], '--ask-for-approval', value['approval']]
    if tab.provider == 'claude':
        return ['--permission-mode', claude_permissions(tab.permissions)['mode']]
    return []


def label(tab):
    if tab.provider == 'shell': return ''
    if not tab.permissions: return 'Restricted on reopen · previous permissions unavailable'
    if tab.provider == 'codex':
        value = codex_permissions(tab.permissions)
        return {'read-only': 'Read only', 'workspace-write': 'Workspace access',
                'danger-full-access': 'Full access'}[value['sandbox']] + ' · saved permissions'
    mode = claude_permissions(tab.permissions)['mode']
    return ('Full access' if mode == 'bypassPermissions' else 'Plan only' if mode == 'plan' else 'Standard permissions') + ' · saved permissions'
