"""desk: CLI for the desk agent. Runs unelevated; the agent does the elevated work.

  desk start [--profile NAME] [--allow a.exe,b.exe]   start the elevated agent (one UAC prompt)
  desk stop | status | windows | profiles
  desk notes                     app notes of the active profile (names, pitfalls)
  desk snapshot [-w WIN] [--diff] [--no-web] [--max N] [--root REF] [-i]
                                 --root: only that element's subtree, -i: only lines with refs
  desk find "text" [--role button|tab|edit|row|...] [-w WIN]   matching elements only
  desk wait --text T | --gone T | --title T | --stable  [--timeout S] [-w WIN]
  desk click REF [--at X,Y] [--confirm] [--diff]   REF = e12 (element) or w1 (window, needs --at)
  desk type REF "text" [--diff]  inputs, selects (option text or value), edits
  desk key REF enter|tab|esc|... [--confirm] [--diff]
                                 --diff on an action: wait until the window settles, print what changed
  desk read REF                  text / table rows / input value
  desk shot [WIN|REF] [-o out.png]
  desk js [-w WIN] "expression" [--confirm]   evaluate in the window's web view
  desk acc WIN|REF [--max N]     full MSAA tree (ribbons, toolbars, grids; all grid rows)
  desk doctor                    diagnose: privileges, target app, windows, policy, errors
  desk mcp                       run as an MCP server on stdio (for AI clients)
  desk skill                     print the usage guide for AI agents (skills/desk-agent/SKILL.md)
  desk reload                    pick up agent code changes without UAC

Errors start with a code and a retry verdict, e.g. `STALE_REF retry=safe: ...` (see errors.py).
Policy (policy.json): clicks/keys on targets labelled like 발송, 삭제, 퀵수집 ... are refused
(POLICY_DENIED) unless --confirm is given. Only add --confirm after the user agreed to that exact action.
PowerShell: write refs as e12 or '@e12' (a bare @e12 is PowerShell splatting).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "desk-agent", "agent.json")


def load_state() -> dict | None:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def send(req: dict, timeout: float = 120) -> dict:
    st = load_state()
    if not st:
        raise SystemExit("agent is not running - run: desk start")
    req = dict(req, token=st["token"])
    try:
        with socket.create_connection(("127.0.0.1", st["port"]), timeout=timeout) as s:
            s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        raise SystemExit(f"cannot reach agent ({e}) - run: desk start")
    return json.loads(buf.decode("utf-8"))


def alive() -> bool:
    try:
        return send({"cmd": "ping"}, timeout=3).get("ok", False)
    except SystemExit:
        return False


def start(profile: str | None, allow: str | None) -> None:
    if load_state() and alive():
        print("already running: " + send({"cmd": "ping"})["out"])
        return
    py = os.path.join(HERE, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(py):
        py = sys.executable.replace("python.exe", "pythonw.exe")
    before = (load_state() or {}).get("started", 0)
    args = "-m deskagent.agent" + (f' --profile "{profile}"' if profile else "") + (f' --allow "{allow}"' if allow else "")
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", py, args, HERE, 0)
    if rc <= 32:
        raise SystemExit(f"could not start agent (ShellExecute {rc}); UAC was declined?")
    print("approve the UAC prompt...", flush=True)
    for _ in range(120):
        time.sleep(1)
        st = load_state()
        if st and st.get("started", 0) > before and alive():
            print("started: " + send({"cmd": "ping"})["out"])
            return
    raise SystemExit("agent did not come up in 120s; see %LOCALAPPDATA%\\desk-agent\\agent.log")


def doctor() -> None:
    """Works even when the agent is down: then it explains why and what to do."""
    st = load_state()
    if not st:
        print(f"WARN agent not running (no {STATE_FILE}) -> desk start")
        return
    if not alive():
        print(f"WARN agent state file exists but the agent does not answer (pid {st.get('pid')}).")
        print("     It crashed or was killed; see %LOCALAPPDATA%\\desk-agent\\agent.log, then: desk start")
        return
    print(send({"cmd": "doctor"})["out"])


def parse_at(s: str | None):
    if not s:
        return None
    x, y = s.split(",")
    return [int(x), int(y)]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start"); p.add_argument("--profile"); p.add_argument("--allow")
    sub.add_parser("profiles"); sub.add_parser("notes")
    sub.add_parser("stop"); sub.add_parser("status"); sub.add_parser("windows"); sub.add_parser("reload")
    p = sub.add_parser("snapshot"); p.add_argument("-w", "--window"); p.add_argument("--diff", action="store_true")
    p.add_argument("--no-web", action="store_true"); p.add_argument("--max", type=int, default=300)
    p.add_argument("--root"); p.add_argument("-i", "--interactive", action="store_true")
    p = sub.add_parser("find"); p.add_argument("query"); p.add_argument("--role"); p.add_argument("-w", "--window")
    p.add_argument("--no-web", action="store_true")
    p = sub.add_parser("wait"); p.add_argument("--text"); p.add_argument("--gone"); p.add_argument("--title")
    p.add_argument("--stable", action="store_true"); p.add_argument("--timeout", type=float, default=10)
    p.add_argument("-w", "--window"); p.add_argument("--no-web", action="store_true")
    p = sub.add_parser("click"); p.add_argument("ref"); p.add_argument("--at"); p.add_argument("--mode", choices=["post", "send"])
    p.add_argument("--confirm", action="store_true"); p.add_argument("--diff", action="store_true")
    p = sub.add_parser("type"); p.add_argument("ref"); p.add_argument("text"); p.add_argument("--diff", action="store_true")
    p = sub.add_parser("key"); p.add_argument("ref"); p.add_argument("key"); p.add_argument("--confirm", action="store_true")
    p.add_argument("--diff", action="store_true")
    sub.add_parser("skill")
    p = sub.add_parser("read"); p.add_argument("ref")
    p = sub.add_parser("shot"); p.add_argument("target", nargs="?"); p.add_argument("-o", "--out")
    p = sub.add_parser("js"); p.add_argument("-w", "--window"); p.add_argument("expr"); p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("acc"); p.add_argument("target"); p.add_argument("--max", type=int, default=300)
    sub.add_parser("doctor")
    sub.add_parser("mcp", help="run as an MCP server on stdio")
    a = ap.parse_args()

    if a.cmd == "mcp":
        from deskagent import mcp_server
        mcp_server.main(); return
    if a.cmd == "doctor":
        doctor(); return
    if a.cmd == "skill":
        with open(os.path.join(HERE, "skills", "desk-agent", "SKILL.md"), encoding="utf-8") as f:
            print(f.read()); return
    if a.cmd == "start":
        start(a.profile, a.allow); return
    if a.cmd == "profiles":
        from deskagent import profiles
        d = profiles.default_name()
        for n in profiles.available():
            print(f"{n}{'  (default)' if n == d else ''}  {profiles.path_of(n)}")
        return
    if a.cmd == "status":
        print(send({"cmd": "ping"}, timeout=5)["out"] if load_state() and alive() else "not running"); return
    if a.cmd == "stop":
        if load_state() and alive():
            send({"cmd": "shutdown"})
        print("stopped"); return

    req: dict = {"cmd": a.cmd}
    if a.cmd == "snapshot":
        req.update(window=a.window, diff=a.diff, no_web=a.no_web, max=a.max, root=a.root, interactive=a.interactive)
    elif a.cmd == "find":
        req.update(query=a.query, role=a.role, window=a.window, no_web=a.no_web)
    elif a.cmd == "wait":
        req.update(text=a.text, gone=a.gone, title=a.title, stable=a.stable, timeout=a.timeout,
                   window=a.window, no_web=a.no_web)
    elif a.cmd == "click":
        req.update(ref=a.ref, at=parse_at(a.at), mode=a.mode, confirm=a.confirm, diff=a.diff)
    elif a.cmd == "type":
        req.update(ref=a.ref, text=a.text, diff=a.diff)
    elif a.cmd == "key":
        req.update(ref=a.ref, key=a.key, confirm=a.confirm, diff=a.diff)
    elif a.cmd == "read":
        req.update(ref=a.ref)
    elif a.cmd == "shot":
        req.update(target=a.target, path=os.path.abspath(a.out) if a.out else None)
    elif a.cmd == "js":
        req.update(window=a.window, expr=a.expr, confirm=a.confirm)
    elif a.cmd == "acc":
        req.update(target=a.target, max=a.max)
    resp = send(req, timeout=max(120, (a.timeout + 30) if a.cmd == "wait" else 0))
    print(clean(resp["out"]))
    if not resp.get("ok"):
        sys.exit(1)


def clean(out: str) -> str:
    """An agent started before the coded-error change prefixes errors with the exception class name."""
    for prefix in ("DeskFail: ", "Blocked: ", "DeskError: "):
        if out.startswith(prefix):
            return out[len(prefix):]
    return out


if __name__ == "__main__":
    main()
