from dataclasses import replace
from types import SimpleNamespace
import unittest
import test_core
from parking.model import Window,Tab
from parking.providers import Providers

class TerminalNames(unittest.TestCase):
    def setUp(self): test_core.Fixture.setUp(self)

    def test_raw_name_is_saved_without_becoming_an_identity_input(self):
        original=self.desktop.source.tabs[0]
        provider=SimpleNamespace(name="shell",identify=lambda session,processes:original)
        tab=Providers(provider).identify(dict(tty=original.tty,name="Raw name"),{})
        self.assertEqual(tab.terminal_name,"Raw name")
        self.assertEqual(Tab.decode(tab.encode()).terminal_name,"Raw name")
        self.assertEqual(tab.identity,original.identity)

    def test_restore_passes_raw_name_and_display_title_separately(self):
        source=self.desktop.source
        tab=replace(source.tabs[0],title="Claude Code (claude)",terminal_name="Claude Code")
        source=Window(source.window_id,(tab,)+source.tabs[1:])
        self.desktop.source=source;self.desktop.live[1]=source
        record=self.flow.park(source.window_id,source.fingerprint)
        self.assertEqual(self.flow.reopen(record["id"])["phase"],"returned")
        create=next(args for action,args in self.desktop.calls if action=="create")
        self.assertEqual(create["title"],"Claude Code (claude)")
        self.assertEqual(create["name"],"Claude Code")
