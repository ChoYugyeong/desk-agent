"""End-to-end tests against throwaway test windows (WinForms + WPF). No elevation, no real mouse.

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v

The agent runs in-process here (same integrity as the test windows), so no UAC prompt and no running
agent is needed. Each test window is started by the test and killed afterwards.
Optional: DESK_TEST_NOTEPAD=1 adds a read-only Notepad check when no Notepad is running.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from deskagent import agent as A  # noqa: E402
from deskagent import policy, winlib  # noqa: E402

winlib.set_dpi_aware()
# Keep test state apart from the real agent's %LOCALAPPDATA%\desk-agent (and inside the repo, git-ignored).
A.SNAP_DIR = os.path.join(HERE, ".tmp", "snapshots")
A.SHOT_DIR = os.path.join(HERE, ".tmp", "shots")


def launch(script: str) -> subprocess.Popen:
    return subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(HERE, script)], creationflags=subprocess.CREATE_NO_WINDOW)


class Base(unittest.TestCase):
    title = ""
    script = ""
    allow = ["powershell.exe"]

    @classmethod
    def setUpClass(cls):
        cls.proc = launch(cls.script)
        cls.agent = A.Agent(cls.allow, {"name": "test", "confirm_labels": ["Delete"]})
        deadline = time.time() + 30
        while time.time() < deadline:
            if any(cls.title in t for _, _, t, _ in cls.agent.list_windows()):
                break
            time.sleep(0.3)
        else:
            cls.proc.kill()
            raise unittest.SkipTest(f"{cls.title} did not open")
        time.sleep(0.8)

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        policy.EXTRA = {}

    def run_cmd(self, **req) -> str:
        return self.agent.handle(req)

    def fail_code(self, **req) -> str:
        with self.assertRaises(A.DeskFail) as cm:
            self.agent.handle(req)
        return str(cm.exception).split(" ", 1)[0]

    def snap(self, **kw) -> str:
        return self.run_cmd(cmd="snapshot", window=self.title, **kw)

    def ref(self, pattern: str, text: str | None = None) -> str:
        text = text if text is not None else self.snap()
        m = re.search(r"@(e\d+) " + pattern, text)
        self.assertIsNotNone(m, f"no element matching {pattern!r} in:\n{text}")
        return m.group(1)


class FormsTest(Base):
    title = "desk-test-forms"
    script = "fixture_forms.ps1"

    def test_snapshot_lists_controls(self):
        s = self.snap()
        for want in ('button "Count"', '"Agree"', "edit =", 'label "'):
            self.assertIn(want, s)
        self.assertRegex(s, r'checkbox \[[ x]\] "Agree"')

    def test_click_with_diff_shows_change(self):
        out = self.run_cmd(cmd="click", ref=self.ref('button "Count"'), diff=True)
        self.assertIn("OK BM_CLICK", out)
        self.assertRegex(out, r'\+ .*count=[1-9]')

    def test_type_and_read(self):
        e = self.ref("edit =")
        self.run_cmd(cmd="type", ref=e, text="hello desk")
        self.assertEqual(self.run_cmd(cmd="read", ref=e), "hello desk")

    def test_checkbox(self):
        before = 'checkbox [x] "Agree"' in self.snap()
        self.run_cmd(cmd="click", ref=self.ref(r'checkbox \[[ x]\] "Agree"'))
        time.sleep(0.3)
        self.assertIn(f'checkbox {"[ ]" if before else "[x]"} "Agree"', self.snap())

    def test_policy_blocks_then_confirm(self):
        send = self.ref('button "Send report"')
        self.assertEqual(self.fail_code(cmd="click", ref=send), "POLICY_DENIED")
        time.sleep(0.3)
        self.assertNotIn("SENT", self.snap())
        self.run_cmd(cmd="click", ref=send, confirm=True)
        self.assertIn("OK", self.run_cmd(cmd="wait", text="SENT", window=self.title, timeout=5))

    def test_grid_rows_via_msaa(self):
        s = self.snap()
        self.assertRegex(s, r"apple \| 3")
        row = self.ref(r"[^\n]*apple", s)  # row names are localized ("Row 0", "행 0")
        self.assertIn("apple", self.run_cmd(cmd="read", ref=row))

    def test_find_and_filters(self):
        f = self.run_cmd(cmd="find", query="Count", role="button", window=self.title)
        self.assertIn('button "Count"', f)
        self.assertNotIn("Agree", f)
        i = self.snap(interactive=True)
        self.assertTrue(all("@e" in ln or ln.startswith("[w") for ln in i.splitlines()))

    def test_error_codes(self):
        self.assertEqual(self.fail_code(cmd="click", ref="e999999"), "UNKNOWN_REF")
        self.assertEqual(self.fail_code(cmd="snapshot", window="no-such-window"), "WINDOW_NOT_FOUND")
        self.assertEqual(self.fail_code(cmd="wait", gone="Count", window=self.title, timeout=1), "TIMEOUT")
        w = next(w for w, _, t, _ in self.agent.list_windows() if self.title in t)
        self.assertEqual(self.fail_code(cmd="click", ref=w), "INVALID_ARGS")  # no --at: refused
        self.assertEqual(self.fail_code(cmd="shot", target=w), "WINDOW_MINIMIZED")


class WpfTest(Base):
    title = "desk-test-wpf"
    script = "fixture_wpf.ps1"

    def test_uia_snapshot(self):
        s = self.snap()
        self.assertIn("[uia]", s)
        self.assertRegex(s, r'button "Hello".*\[invoke\]')
        self.assertRegex(s, r'checkbox "Flag".*\[toggle\]')

    def test_uia_invoke(self):
        out = self.run_cmd(cmd="click", ref=self.ref('button "Hello"'), diff=True)
        self.assertIn("Invoke", out)
        self.assertIn('"clicked"', out)

    def test_uia_value_and_toggle(self):
        e = self.ref('edit ""')
        self.run_cmd(cmd="type", ref=e, text="wpf text")
        self.assertEqual(self.run_cmd(cmd="read", ref=e), "wpf text")
        self.run_cmd(cmd="click", ref=self.ref('checkbox "Flag"'))
        time.sleep(0.3)
        self.assertRegex(self.snap(), r'checkbox "Flag" \(checked\)')

    def test_uia_policy_from_profile(self):
        d = self.ref('button "Delete all"')
        self.assertEqual(self.fail_code(cmd="click", ref=d), "POLICY_DENIED")
        time.sleep(0.3)
        self.assertNotIn("DELETED", self.snap())


@unittest.skipUnless(os.environ.get("DESK_TEST_NOTEPAD") == "1", "set DESK_TEST_NOTEPAD=1 to run")
class NotepadReadOnlyTest(unittest.TestCase):
    """Read-only: Notepad may join an existing window and restore unsaved tabs, so nothing is typed."""

    def test_notepad_readable(self):
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq notepad.exe"], capture_output=True, text=True).stdout
        if "notepad.exe" in out.lower():
            self.skipTest("Notepad is already running; not touching the user's window")
        p = subprocess.Popen(["notepad.exe"])
        try:
            ag = A.Agent(["notepad.exe"], {})
            for _ in range(50):
                if ag.list_windows():
                    break
                time.sleep(0.2)
            s = ag.handle({"cmd": "snapshot"})
            self.assertRegex(s, r"@e\d+ (edit|document|menuitem|button)")
        finally:
            p.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)
