"""Local settings. No agent configuration or terminal preferences are modified."""
import json
from pathlib import Path
from .model import ParkingError

DEFAULTS = dict(auto_continue_interrupted=True, continue_message='please continue')

class Settings:
    def __init__(self, root):
        self.path = Path(root) / 'settings.json'

    def read(self):
        if not self.path.exists(): return dict(DEFAULTS)
        return self.validate(json.loads(self.path.read_text()))

    def read_optional(self):
        """Broken optional message preferences must not prevent session recovery."""
        try: return self.read()
        except (OSError, ValueError, ParkingError):
            return dict(DEFAULTS, auto_continue_interrupted=False)

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or set(value) != set(DEFAULTS):
            raise ParkingError('Invalid Free Parking settings.')
        if type(value['auto_continue_interrupted']) is not bool:
            raise ParkingError('Auto-continue must be on or off.')
        message = value['continue_message']
        if not isinstance(message, str) or not message.strip() or len(message) > 2000 or '\0' in message:
            raise ParkingError('Continue message must contain 1–2000 characters.')
        return dict(value)

    def save(self, value):
        from .store import atomic_json
        value = self.validate(value)
        atomic_json(self.path, value)
        return self.read()


def continuation_message(tab, interrupted, settings):
    # A busy snapshot is NOT proof that parking actually stopped the turn.
    if (tab.provider not in ('claude', 'codex') or not settings['auto_continue_interrupted']
            or not isinstance(interrupted, dict) or interrupted.get('outcome') != 'interrupted'
            or interrupted.get('session_id') != tab.session_id
            or interrupted.get('provider') != tab.provider):
        return None
    return settings['continue_message']
