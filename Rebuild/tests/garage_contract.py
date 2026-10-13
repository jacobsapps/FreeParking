"""Emit simulated-OS responses from the real bridge for Swift decoding tests."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_garage import GarageTests
from parking.garage import dispatch

fixture = GarageTests()
fixture.setUp()
try:
    with patch('parking.garage.compose', return_value=(fixture.store, fixture.desktop, fixture.flow)):
        scanned = dispatch(['--live', 'scan'])
        parked = dispatch(['--live', 'park', fixture.first.window_id, fixture.first.fingerprint])
        returned = dispatch(['--live', 'restore', parked['cars'][0]['id']])
        print(json.dumps([scanned, parked, returned]))
finally:
    fixture.doCleanups()
