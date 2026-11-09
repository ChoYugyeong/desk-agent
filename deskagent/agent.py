"""desk agent: runs elevated, listens on 127.0.0.1, drives allow-listed apps without the real mouse.

Start through `desk start` (UAC prompt once). Protocol: one JSON object per line, with token.
"""
from __future__ import annotations

import argparse
import difflib
import gc
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import traceback

from . import errors, iedom, msaa, policy, profiles, uia, winlib

# `desk reload` re-runs this module; reload helper modules the running serve loop does not know about.
# Order matters: msaa and policy import DeskError from errors.
import importlib as _importlib
errors = _importlib.reload(errors)
msaa = _importlib.reload(msaa)
policy = _importlib.reload(policy)
profiles = _importlib.reload(profiles)
uia = _importlib.reload(uia)

STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "desk-agent")
STATE_FILE = os.path.join(STATE_DIR, "agent.json")
LOG_FILE = os.path.join(STATE_DIR, "agent.log")
SHOT_DIR = os.path.join(STATE_DIR, "shots")
SNAP_DIR = os.path.join(STATE_DIR, "snapshots")  # last snapshot per window, for --diff (kept on disk, not RAM)

# Resource limits. The agent mostly sits idle between commands, so it gives its RAM back while idle.
MAX_REFS = 3000      # remembered element refs; the least recently seen are forgotten beyond this
IDLE_SECONDS = 20    # no command for this long -> background priority + working set trimmed to the page file
IDLE_CHECK = 5

SKIP_SUBTREE = ("CefBrowserWindow", "Chrome_WidgetWin_0", "Chrome_WidgetWin_1")
CONTAINERS = ("Shell Embedding", "Shell DocObject View")
WEB_CLASS = "Internet Explorer_Server"


def norm_class(cls: str) -> str:
    if cls.startswith("WindowsForms10."):
        return cls.split(".")[1]
    return cls


class Agent:
    def __init__(self, allow: list[str], prof: dict | None = None):
        self.allow = {a.lower() for a in allow}
        self.profile = prof or {}
        self.refs: dict[str, dict] = {}      # "e12" -> info
        self.keys: dict[tuple, str] = {}     # stable identity -> "e12"
        self.next_ref = 1
        self.windows: dict[str, int] = {}    # "w1" -> hwnd
        self.wkeys: dict[int, str] = {}
        self.next_win = 1
        self.gen = 0

    # ----- discovery -----
    def allowed_pids(self) -> dict[int, str]:
        pids: dict[int, str] = {}
        for h in winlib.all_top_windows():
            pid = winlib.window_pid(h)
            if pid in pids:
                continue
            name = winlib.process_name(pid)
            if name.lower() in self.allow:
                pids[pid] = name
        return pids

    def win_ref(self, hwnd: int) -> str:
        if hwnd not in self.wkeys:
            k = f"w{self.next_win}"
            self.next_win += 1
            self.wkeys[hwnd] = k
            self.windows[k] = hwnd
        return self.wkeys[hwnd]

    def ref_for(self, key: tuple, info: dict) -> str:
        r = self.keys.get(key)
        if r is None:
            r = f"e{self.next_ref}"
            self.next_ref += 1
            self.keys[key] = r
        self.refs[r] = info
        self.__dict__.setdefault("ref_seen", {})[r] = self.gen
        return r

    def prune_refs(self) -> None:
        """Forget the least recently seen refs once there are more than MAX_REFS (long sessions, big grids)."""
        seen = self.__dict__.setdefault("ref_seen", {})
        if len(self.refs) <= MAX_REFS:
            return
        keep = sorted(self.refs, key=lambda r: seen.get(r, -1), reverse=True)[: int(MAX_REFS * 0.7)]
        keep_set = set(keep)
        self.refs = {r: self.refs[r] for r in keep}
        self.keys = {k: r for k, r in self.keys.items() if r in keep_set}
        self.ref_seen = {r: seen.get(r, -1) for r in keep}

    # ----- snapshot history on disk -----
    @staticmethod
    def _snap_path(key: str) -> str:
        return os.path.join(SNAP_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".txt")

    def load_last(self, key: str) -> list[str] | None:
        try:
            with open(self._snap_path(key), encoding="utf-8") as f:
                return f.read().split("\n")
        except OSError:
            return None

    def save_last(self, key: str, lines: list[str]) -> None:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(self._snap_path(key), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def list_windows(self) -> list[tuple[str, int, str, str]]:
        pids = self.allowed_pids()
        out = []
        for h in winlib.top_windows(set(pids)):
            title = winlib.get_text(h, 200)
            cls = winlib.class_name(h)
            if not title and cls != "#32770":
                continue
            out.append((self.win_ref(h), h, title, pids[winlib.window_pid(h)]))
        return out

    def resolve_window(self, sel: str | None) -> list[tuple[str, int, str, str]]:
        wins = self.list_windows()
        if not sel:
            return wins
        s = sel.lower()
        hit = [w for w in wins if w[0] == s or str(w[1]) == s or s in w[2].lower()]
        if not hit:
            raise errors.window_not_found(sel)
        return hit

    # ----- snapshot -----
    def snap_window(self, wref: str, top: int, title: str, proc: str, web: bool, max_items: int) -> list[str]:
        l, t, r, b = winlib.rect(top)
        kind = " dialog" if winlib.class_name(top) == "#32770" else ""
        lines = [f'[{wref}]{kind} "{title}" ({proc}) {r - l}x{b - t}' + (" (minimized)" if winlib.user32.IsIconic(top) else "")]
        kids = winlib.child_windows(top)
        par = {k: winlib.parent(k) for k in kids}
        has_vis_child = set()
        for k in kids:
            if winlib.visible(k):
                has_vis_child.add(par[k])
        skip_roots: set[int] = set()

        def under_skip(h: int) -> bool:
            p = par.get(h, 0)
            while p and p != top:
                if p in skip_roots:
                    return True
                p = par.get(p, 0) or winlib.parent(p)
            return False

        for h in kids:
            if not winlib.visible(h) or under_skip(h):
                continue
            raw_cls = winlib.class_name(h)
            cls = norm_class(raw_cls)
            if raw_cls in SKIP_SUBTREE:
                skip_roots.add(h)
                if raw_cls == "CefBrowserWindow":
                    lines.append("  (embedded Chromium view: not readable without remote debugging)")
                continue
            if raw_cls == WEB_CLASS:
                skip_roots.add(h)
                if web:
                    lines.extend(self.snap_web(h, max_items))
                else:
                    lines.append("  [web] (skipped, --no-web)")
                continue
            if raw_cls in CONTAINERS:
                continue
            if raw_cls in uia.XAML_HOSTS:
                skip_roots.add(h)
                ul = self.try_uia(h, max_items, skip_child_windows=False)
                if ul:
                    lines.append(f'  [uia] "{raw_cls}"')
                    lines.extend(ul)
                continue
            text = winlib.get_text(h, 200).replace("\r", " ").replace("\n", " ").strip()
            disp_text = text if len(text) <= 50 else text[:50] + "..."
            dis = "" if winlib.enabled(h) else " (disabled)"
            st = winlib.style(h)
            info = {"kind": "win32", "hwnd": h, "top": top}
            up = cls.upper()
            if up == "BUTTON":
                bs = st & 0xF
                if bs == 7:
                    if text:
                        lines.append(f'  group "{disp_text}"')
                    continue
                if bs in (2, 3, 5, 6):
                    chk = winlib.send_get(h, winlib.BM_GETCHECK)
                    info["ctl"] = "check"
                    ref = self.ref_for(("w", h), info)
                    lines.append(f'  @{ref} checkbox {"[x]" if chk else "[ ]"} "{disp_text}"{dis}')
                elif bs in (4, 9):
                    chk = winlib.send_get(h, winlib.BM_GETCHECK)
                    info["ctl"] = "radio"
                    ref = self.ref_for(("w", h), info)
                    lines.append(f'  @{ref} radio {"[x]" if chk else "[ ]"} "{disp_text}"{dis}')
                else:
                    # WinForms and other toolkits draw check boxes on push-button styles; MSAA knows the real role.
                    role, checked = 0, False
                    try:
                        n = msaa.Node(msaa.from_window(h))
                        role, checked = n.role_id, bool(n.state & (msaa.STATE_CHECKED | msaa.STATE_PRESSED))
                    except Exception:  # noqa: BLE001
                        pass
                    if role in (44, 45):
                        info["ctl"] = "check" if role == 44 else "radio"
                        ref = self.ref_for(("w", h), info)
                        kind_name = "checkbox" if role == 44 else "radio"
                        lines.append(f'  @{ref} {kind_name} {"[x]" if checked else "[ ]"} "{disp_text}"{dis}')
                        continue
                    info["ctl"] = "button"
                    ref = self.ref_for(("w", h), info)
                    lines.append(f'  @{ref} button "{disp_text}"{dis}')
            elif up == "EDIT" or up.startswith("RICHEDIT"):
                info["ctl"] = "edit"
                ref = self.ref_for(("w", h), info)
                val = '"***"' if st & 0x20 else f'"{disp_text}"'
                lines.append(f"  @{ref} edit = {val}{dis}")
            elif up == "COMBOBOX":
                info["ctl"] = "combo"
                ref = self.ref_for(("w", h), info)
                lines.append(f'  @{ref} combo = "{disp_text}"{dis}')
            elif up == "STATIC":
                if text:
                    lines.append(f'  label "{disp_text}"')
            else:
                w, hh = winlib.client_size(h)
                leaf = h not in has_vis_child
                acc_lines = self.try_acc(h, max_items) if w * hh >= 400 else []
                if acc_lines:
                    try:
                        aname = msaa.Node(msaa.from_window(h)).name
                    except Exception:  # noqa: BLE001
                        aname = ""
                    lines.append(f'  [acc] "{aname or cls}" {w}x{hh}')
                    lines.extend(acc_lines)
                    continue
                if leaf and w * hh >= 400:
                    ul = self.try_uia(h, max_items, skip_child_windows=True)
                    if ul:
                        lines.append(f'  [uia] "{cls}" {w}x{hh}')
                        lines.extend(ul)
                        continue
                    info["ctl"] = "custom"
                    ref = self.ref_for(("w", h), info)
                    label = f' "{disp_text}"' if text else ""
                    lines.append(f"  @{ref} custom{label} <{cls}> {w}x{hh} (click --at x,y inside){dis}")
                elif text and leaf:
                    lines.append(f'  label "{disp_text}"')
        if not any("@e" in ln for ln in lines[1:]):
            # Nothing actionable through window handles or MSAA (WPF, WinUI, UWP): read the window with UIA.
            ul = self.try_uia(top, max_items, skip_child_windows=False)
            if ul:
                lines.append("  [uia]")
                lines.extend(ul)
        return lines

    def snap_uia(self, hwnd: int, max_items: int, indent: str = "    ", skip_child_windows: bool = False) -> list[str]:
        lines: list[str] = []
        top = winlib.root(hwnd)
        for depth, path, node in uia.walk(hwnd, max_items, skip_child_windows):
            role, name = node.role, node.name
            acts, flags, val = node.actions(), node.flags(), node.value()
            if role in uia.STRUCTURE and not name and not acts:
                continue
            if role == "text" and not name:
                continue
            pad = indent + "  " * (depth - 1)
            if not acts and role in ("text", "group", "pane", "header", "statusbar"):
                lines.append(f'{pad}{role} "{name}"')  # plain text: no ref needed
                continue
            rid = node.runtime_id
            ref = self.ref_for(("uia", hwnd, rid), {"kind": "uia", "hwnd": hwnd, "rid": rid, "top": top,
                                                     "name": name, "role": role})
            v = f' = "{val[:50]}"' if val is not None else ""
            fl = f' ({", ".join(flags)})' if flags else ""
            a = f' [{",".join(acts)}]' if acts else ""
            lines.append(f'{pad}@{ref} {role} "{name}"{v}{fl}{a}')
        return lines

    def try_uia(self, h: int, max_items: int, skip_child_windows: bool) -> list[str]:
        try:
            lines = self.snap_uia(h, max_items, skip_child_windows=skip_child_windows)
        except Exception:  # noqa: BLE001
            return []
        return lines if any("@e" in ln for ln in lines) else []

    def uia_dump(self, target: str, max_items: int) -> str:
        r = target.lstrip("@")
        h = self.windows[r] if r in self.windows else (self.get_ref(target).get("hwnd") or self.get_ref(target).get("ie"))
        return "\n".join(self.snap_uia(h, max_items, indent="  ")) or "(no UI Automation content)"

    def uia_node(self, info: dict) -> "uia.Node":
        node = uia.find(info["hwnd"], info["rid"])
        if node.name != info["name"]:
            raise errors.stale_ref(info["name"], "the element changed")
        return node

    def snap_web(self, ie: int, max_items: int) -> list[str]:
        self.gen += 1
        try:
            title, url = iedom.page_info(ie)
        except Exception as e:  # noqa: BLE001
            return [f"  [web] (not readable: {e})"]
        short = url.split("?")[0] + ("?..." if "?" in url else "")
        lines = [f'  [web] "{title}" {short}']
        try:
            items = iedom.snapshot(ie, self.gen, max_items)
        except Exception as e:  # noqa: BLE001
            return lines + [f"    (script failed: {e})"]
        seen: dict[str, int] = {}
        for temp, key, disp in items:
            if temp == "#":
                lines.append(f"    {disp}")
                continue
            seen[key] = seen.get(key, 0) + 1
            ref = self.ref_for(("web", ie, key, seen[key]), {"kind": "web", "ie": ie, "id": temp, "top": winlib.root(ie),
                                                             "label": disp})
            lines.append(f"    @{ref} {disp}")
        return lines

    def collect(self, window: str | None, web: bool, max_items: int) -> list[str]:
        """Read the windows once and hand out refs, without touching the --diff baseline."""
        lines: list[str] = []
        for wref, h, title, proc in self.resolve_window(window):
            lines.extend(self.snap_window(wref, h, title, proc, web, max_items))
        self.gen += 1  # marks which refs this read saw
        self.prune_refs()
        return lines

    @staticmethod
    def filter_lines(lines: list[str], root: str | None, interactive: bool) -> list[str]:
        """--root @eN keeps that element and everything indented under it; -i keeps lines with refs."""
        if root:
            tag = "@" + root.lstrip("@") + " "
            start = next((i for i, ln in enumerate(lines) if tag in ln + " "), None)
            if start is None:
                raise errors.stale_ref(root, "not in the current snapshot")
            depth = len(lines[start]) - len(lines[start].lstrip())
            sub = [lines[start]]
            for ln in lines[start + 1:]:
                if len(ln) - len(ln.lstrip()) <= depth:
                    break
                sub.append(ln)
            lines = sub
        if interactive:
            lines = [ln for ln in lines if "@e" in ln or ln.startswith("[w")]
        return lines

    @staticmethod
    def diff_lines(prev: list[str], cur: list[str]) -> str:
        out = [op for op in difflib.ndiff(prev, cur) if op.startswith(("+ ", "- "))]
        return "\n".join(out) if out else "(no changes)"

    def snapshot(self, window: str | None, diff: bool, web: bool, max_items: int,
                 root: str | None = None, interactive: bool = False) -> str:
        lines = self.collect(window, web, max_items)
        if not lines:
            return "(no visible windows for allowed apps: " + ", ".join(sorted(self.allow)) + ")"
        key = window or "*"
        prev = self.load_last(key)
        self.save_last(key, lines)
        cur = self.filter_lines(lines, root, interactive)
        if not diff or prev is None:
            return "\n".join(cur)
        try:
            prev = self.filter_lines(prev, root, interactive)
        except errors.DeskError:
            prev = []
        return self.diff_lines(prev, cur)

    def find(self, query: str, role: str | None, window: str | None, web: bool, limit: int = 40) -> str:
        """Matching elements only (text, name or value contains query; optional role), with refs."""
        q = query.lower()
        out, header, total = [], "", 0
        for ln in self.collect(window, web, 2000):
            if ln.startswith("[w"):
                header = ln
                continue
            body = ln.strip()
            if "@e" not in body or q not in body.lower():
                continue
            if role:
                parts = body.split(" ", 2)
                if len(parts) < 2 or parts[1].lower() != role.lower():
                    continue
            total += 1
            if total > limit:
                continue
            if header:
                out.append(header)
                header = ""
            out.append("  " + body)
        if not total:
            return f"(no element matches {query!r}" + (f" with role {role}" if role else "") + ")"
        if total > limit:
            out.append(f"... {total - limit} more matches; narrow the query or add --role")
        return "\n".join(out)

    def wait(self, req: dict) -> str:
        """Block until a condition holds instead of sleeping a fixed time."""
        timeout = float(req.get("timeout") or 10)
        window, web = req.get("window"), not req.get("no_web")
        text, gone, title, stable = req.get("text"), req.get("gone"), req.get("title"), req.get("stable")
        modes = [m for m in (text, gone, title, stable) if m]
        if len(modes) != 1:
            raise errors.invalid("wait takes exactly one of --text, --gone, --title, --stable")
        t0 = time.monotonic()
        prev = None
        while True:
            el = time.monotonic() - t0
            if title:
                hit = [t for _, _, t, _ in self.list_windows() if title.lower() in t.lower()]
                if hit:
                    return f"OK window {hit[0]!r} after {el:.1f}s"
            else:
                lines = self.collect(window, web, 2000)
                if text:
                    hit = [ln.strip() for ln in lines if text.lower() in ln.lower()]
                    if hit:
                        return f"OK after {el:.1f}s\n" + "\n".join(hit[:10])
                elif gone:
                    if not any(gone.lower() in ln.lower() for ln in lines):
                        return f"OK {gone!r} gone after {el:.1f}s"
                elif stable:
                    if prev is not None and lines == prev:
                        return f"OK stable after {el:.1f}s"
                    prev = lines
            if el >= timeout:
                what = f"text {text!r}" if text else f"{gone!r} gone" if gone else f"window {title!r}" if title else "stable UI"
                raise errors.timeout(what, timeout)
            time.sleep(0.4)

    def act_then_diff(self, info_ref: str, action) -> str:
        """Run an action, wait until the window stops changing, and return what changed (one round trip)."""
        info = self.get_ref(info_ref)
        wref = self.win_ref(info["top"])
        # A fresh baseline: a saved snapshot may be old or taken with other options (--no-web).
        before = self.collect(wref, True, 300)
        out = action()
        time.sleep(0.3)
        lines, prev, t0 = None, None, time.monotonic()
        while time.monotonic() - t0 < 5:
            lines = self.collect(wref, True, 300)
            if lines == prev:
                break
            prev = lines
            time.sleep(0.4)
        self.save_last(wref, lines)
        return out + "\n--- changes in " + wref + ":\n" + self.diff_lines(before, lines)

    def snap_acc(self, hwnd: int, max_items: int, indent: str = "  ", skip_windows: bool = False,
                 max_rows: int = 10) -> list[str]:
        """MSAA tree of one window (owner-drawn controls like ribbons, toolbars and grids)."""
        lines: list[str] = []
        rows_seen: dict[tuple, int] = {}
        cols: dict[tuple, msaa.Columns] = {}
        more_at: dict[tuple, tuple[int, str]] = {}
        top = winlib.root(hwnd)
        for depth, path, node in msaa.walk(hwnd, max_items, skip_windows=skip_windows):
            role, name, flags = msaa.describe(node)
            rid = node.role_id
            if rid in msaa.STRUCTURE_ROLES and not name:
                continue
            fl = (" (" + ", ".join(flags) + ")") if flags else ""
            # name and role are part of the identity: the same path can hold another element after a tab switch
            ref = self.ref_for(("acc", hwnd, path, rid, name), {"kind": "acc", "hwnd": hwnd, "path": path, "top": top,
                                                                 "name": name, "role": rid})
            if rid in msaa.ROW_ROLES:
                parent_key = path[:-1]
                rows_seen[parent_key] = rows_seen.get(parent_key, 0) + 1
                n = rows_seen[parent_key]
                pad = indent + "  " * (depth - 1)
                if n > max_rows:
                    if n == max_rows + 1:
                        more_at[parent_key] = (len(lines), pad)
                        lines.append("")
                    continue
                if parent_key not in cols:
                    cols[parent_key] = msaa.Columns(node)
                cells = msaa.row_cells(node, cols[parent_key])
                lines.append(f'{pad}@{ref} {name}: {" | ".join(cells)}{fl}')
                continue
            act = node.default_action
            act = f" [{act}]" if act else ""
            val = ""
            if rid in (42, 46):
                v = " ".join(node.value.split())
                val = f' = "{v[:50]}"'
            lines.append(f'{indent}{"  " * (depth - 1)}@{ref} {role} "{name}"{val}{fl}{act}')
        for key, (i, pad) in more_at.items():
            lines[i] = f"{pad}... {rows_seen[key] - max_rows} more rows (desk acc on this window lists all)"
        return lines

    def try_acc(self, h: int, max_items: int) -> list[str]:
        """MSAA items of a custom window, if it exposes any actionable or named content."""
        try:
            lines = self.snap_acc(h, max_items, indent="    ", skip_windows=True)
        except Exception:  # noqa: BLE001
            return []
        return lines

    def acc(self, target: str, max_items: int) -> str:
        r = target.lstrip("@")
        if r in self.windows:
            h = self.windows[r]
        else:
            info = self.get_ref(target)
            h = info.get("hwnd") or info.get("ie")
        return "\n".join(self.snap_acc(h, max_items)) or "(no accessible children)"

    def acc_node(self, info: dict) -> msaa.Node:
        node = msaa.resolve(info["hwnd"], info["path"])
        # Paths can shift when the app rebuilds its tree; check the element is still the same one.
        if node.role_id != info["role"] or node.name.strip() != info["name"]:
            raise errors.stale_ref(info.get("name", "element"), "the element at that place changed")
        return node

    # ----- actions -----
    def get_ref(self, ref: str) -> dict:
        r = ref.lstrip("@")
        if r.startswith("w") and r in self.windows:
            return {"kind": "window", "hwnd": self.windows[r], "top": self.windows[r]}
        info = self.refs.get(r)
        if not info:
            raise errors.unknown_ref(ref)
        h = info.get("hwnd") or info.get("ie")
        if not winlib.user32.IsWindow(h):
            raise errors.stale_ref(ref, "its window is gone")
        return info

    def label_of(self, info: dict) -> str:
        """What the policy matches against: the element's visible text or accessible name."""
        if info["kind"] == "web":
            return info.get("label", "")
        if info["kind"] in ("acc", "uia"):
            return info.get("name", "")
        return winlib.get_text(info["hwnd"], 200)

    def click(self, ref: str, at: list[int] | None, mode: str = "post", confirm: bool = False) -> str:
        info = self.get_ref(ref)
        if at:
            policy.check_flag("confirm_at_clicks", "a coordinate click", confirm)
        else:
            policy.check_label(self.label_of(info), f"click {ref}", confirm)
        if info["kind"] == "web" and not at:
            return iedom.act(info["ie"], info["id"], "click")
        if info["kind"] == "acc" and not at:
            node = self.acc_node(info)
            if node.default_action:
                node.do_default()
                return f"OK accDoDefaultAction ({node.default_action})"
            node.select()
            return "OK accSelect"
        if info["kind"] == "uia" and not at:
            return "OK UIA " + uia.do_default(self.uia_node(info))
        h = info.get("hwnd") or info.get("ie")
        if not at and (info["kind"] == "window" or info.get("ctl") == "custom"):
            # The middle of a whole window or an owner-drawn area is not a target anyone chose.
            raise errors.invalid(f"{ref} is a window or a drawn area: give --at x,y read off `desk shot {ref}`")
        if info.get("ctl") in ("button", "check", "radio") and not at:
            winlib.button_click(h)
            return "OK BM_CLICK"
        if at:
            # --at uses the same pixel space as `desk shot` of the same ref (window or element),
            # so a point read off the screenshot can be clicked directly. Convert to client coords.
            x, y = winlib.window_to_client(h, at[0], at[1])
            # A coordinate click on a real child control (e.g. a WinForms button) is checked by that control's text.
            target, _, _ = winlib.deepest_child_at(h, x, y)
            if target and target != h:
                policy.check_label(winlib.get_text(target, 200), f"click {ref} --at", confirm)
        else:
            w, hh = winlib.client_size(h)
            x, y = w // 2, hh // 2
        target, tx, ty = winlib.message_click(h, x, y, mode=mode)
        return f"OK message click at {x},{y} -> {winlib.class_name(target)} ({tx},{ty})"

    def type_text(self, ref: str, text: str) -> str:
        info = self.get_ref(ref)
        if info["kind"] == "web":
            return iedom.act(info["ie"], info["id"], "type", text)
        if info.get("ctl") in ("edit", "combo"):
            return "OK WM_SETTEXT" if winlib.set_text(info["hwnd"], text) else "ERR WM_SETTEXT failed"
        if info["kind"] == "acc":
            node = self.acc_node(info)
            node.set_value(text)
            return f'OK accValue = "{node.value}"'
        if info["kind"] == "uia":
            return "OK UIA " + uia.set_value(self.uia_node(info), text)
        raise errors.not_supported("type works on edit/combo/web inputs and accessible edits only")

    def key(self, ref: str, key: str, confirm: bool = False) -> str:
        info = self.get_ref(ref)
        policy.check_key(key, confirm)
        if key.lower() in ("enter", "space"):  # pressing a focused button is the same as clicking it
            policy.check_label(self.label_of(info), f"key {key} on {ref}", confirm)
        if info["kind"] == "web":
            return iedom.act(info["ie"], info["id"], "key", key)
        winlib.post_key(info["hwnd"], key)
        return f"OK posted key {key}"

    def read(self, ref: str) -> str:
        info = self.get_ref(ref)
        if info["kind"] == "web":
            return iedom.act(info["ie"], info["id"], "read")
        if info["kind"] == "acc":
            node = self.acc_node(info)
            if info["role"] in msaa.ROW_ROLES:
                return "\t".join(msaa.row_cells(node, msaa.Columns(node)))
            return node.value or node.name
        if info["kind"] == "uia":
            return uia.read(self.uia_node(info))
        return winlib.get_text(info["hwnd"], 20000)

    def shot(self, target: str | None, path: str | None) -> str:
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = path or os.path.join(SHOT_DIR, time.strftime("shot-%Y%m%d-%H%M%S.png"))
        crop = None
        if target and target.lstrip("@").startswith("e"):
            info = self.get_ref(target)
            top = info["top"]
            tl, tt, _, _ = winlib.rect(top)
            if info["kind"] == "web":
                rc = iedom.act(info["ie"], info["id"], "rect")
                if rc.startswith("ERR"):
                    raise RuntimeError(rc)
                x0, y0, x1, y1 = map(int, rc.split(","))
                il, it, _, _ = winlib.rect(info["ie"])
                crop = (il - tl + x0 - 4, it - tt + y0 - 4, il - tl + x1 + 4, it - tt + y1 + 4)
            else:
                l, t, r, b = winlib.rect(info["hwnd"])
                crop = (l - tl, t - tt, r - tl, b - tt)
        else:
            wins = self.resolve_window(target)
            top = wins[0][1]
        if winlib.user32.IsIconic(top):
            raise errors.minimized()
        w, h = winlib.capture_png(top, path, crop)
        return f"OK {w}x{h} {path}"

    def js(self, window: str | None, expr: str, confirm: bool = False) -> str:
        policy.check_flag("confirm_js", "running page script (it can click anything)", confirm)
        for _, top, _, _ in self.resolve_window(window):
            for k in winlib.child_windows(top):
                if winlib.class_name(k) == WEB_CLASS and winlib.visible(k):
                    return iedom.evaluate(k, expr)
        raise errors.not_supported("no web view in that window")

    # ----- diagnostics -----
    def doctor(self) -> str:
        """One report with the usual causes of 'it does not work': privileges, target app, windows, policy, errors."""
        ok, warn = "ok  ", "WARN"
        out = []
        el = winlib.is_elevated()
        out.append(f"{ok if el else warn} agent elevated={el} pid={os.getpid()}"
                   + ("" if el else "  -> elevated apps cannot be driven; run `desk stop` then `desk start`"))
        prof = self.active_profile()
        out.append(f"{ok if prof else warn} profile {prof.get('name', '(none)')}"
                   + (f" ({prof['_path']})" if prof.get("_path") else "  -> start with --profile NAME")
                   + ("; notes: desk notes" if prof.get("notes") else ""))
        pids = self.allowed_pids()
        if pids:
            out.append(f"{ok} target processes: " + ", ".join(f"{n}({p})" for p, n in sorted(pids.items())))
        else:
            out.append(f"{warn} none of the allowed apps is running: {', '.join(sorted(self.allow))}")
        wins = self.list_windows()
        for w, h, t, p in wins:
            mini = winlib.user32.IsIconic(h)
            out.append(f"{warn if mini else ok} [{w}] {t[:40]!r} ({p})"
                       + ("  minimized -> shot and --at clicks fail until it is restored" if mini else ""))
        try:
            pol = policy.load()
            out.append(f"{ok} policy {pol['_path']}: {len(pol.get('confirm_labels', []))} labels, "
                       f"keys={pol.get('confirm_keys')}, js={'confirm' if pol.get('confirm_js') else 'free'}")
        except RuntimeError as e:
            out.append(f"{warn} {e}")
        ws, priv = winlib.memory_mb()
        res = self.res
        out.append(f"{ok} resources ram={ws:.1f}MB private={priv:.1f}MB refs={len(self.refs)}/{MAX_REFS} "
                   f"idle_trim={'on' if res['thread'] and res['thread'].is_alive() else 'not started'}")
        errs = []
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                entry: list[str] = []
                for ln in f.read().splitlines() + ["2000-01-01 00:00:00,000 END"]:
                    if ln[:4].isdigit() and ln[4:5] == "-":  # a new log record starts
                        expected = any(("DeskFail: " in x and not ("DeskFail: INTERNAL" in x or "DeskFail: APP_ERROR" in x))
                                       or "policy.Blocked: " in x for x in entry)
                        if entry and " ERROR " in entry[0] and not expected:
                            last = next((x.strip() for x in reversed(entry) if x.strip()), "")
                            errs.append(entry[0][:19] + "  " + last)
                        entry = [ln]
                    else:
                        entry.append(ln)
        except OSError:
            pass
        errs = errs[-3:]
        out.append(f"{warn if errs else ok} recent errors in {LOG_FILE}: {len(errs)}")
        out.extend("      " + e[:200] for e in errs)
        return "\n".join(out)

    # ----- resource control -----
    @property
    def res(self) -> dict:
        """Resource state shared by every Agent object: `desk reload` swaps the object but this dict carries over."""
        r = self.__dict__.get("res")
        if r is None:
            r = self.__dict__["res"] = {"lock": threading.Lock(), "last_active": time.monotonic(),
                                        "idle": False, "thread": None, "agent": self}
        return r

    def ensure_idle_thread(self) -> None:
        res = self.res
        res["agent"] = self  # the idle thread always talks to the current (possibly reloaded) object
        t = res["thread"]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_idle_loop, args=(res,), name="desk-idle", daemon=True)
            res["thread"] = t
            t.start()

    def idle_tick(self) -> None:
        res = self.res
        if res["idle"] or time.monotonic() - res["last_active"] < IDLE_SECONDS:
            return
        if not res["lock"].acquire(blocking=False):
            return
        try:
            winlib.set_background(True)
            winlib.trim_memory()
            res["idle"] = True
            logging.info("idle: background mode, working set trimmed")
        finally:
            res["lock"].release()

    def active_profile(self) -> dict:
        if "profile" not in self.__dict__:  # agent started before profiles existed
            name = profiles.default_name()
            try:
                self.profile = profiles.load(name) if name else {}
            except RuntimeError:
                self.profile = {}
        return self.profile

    def handle(self, req: dict) -> str:
        self.ensure_idle_thread()
        policy.EXTRA = self.active_profile()
        res = self.res
        with res["lock"]:
            if res["idle"]:
                winlib.set_background(False)
                res["idle"] = False
            try:
                return self._dispatch(req)
            except DeskFail:
                raise
            except Exception as e:  # noqa: BLE001
                # Every failure leaves with a code and a retry verdict (see errors.py).
                raise DeskFail(errors.classify(e)) from e
            finally:
                gc.collect()  # drop COM proxies of this command now, in the thread that made them
                res["last_active"] = time.monotonic()

    def _dispatch(self, req: dict) -> str:
        cmd = req.get("cmd")
        before = winlib.cursor_pos()
        if cmd == "ping":
            ws, priv = winlib.memory_mb()
            return (f"pong elevated={winlib.is_elevated()} allow={','.join(sorted(self.allow))}"
                    f" ram={ws:.1f}MB private={priv:.1f}MB refs={len(self.refs)}")
        if cmd == "windows":
            rows = self.list_windows()
            return "\n".join(f'[{w}] "{t}" ({p}) hwnd={h}' for w, h, t, p in rows) or "(none)"
        if cmd == "snapshot":
            return self.snapshot(req.get("window"), bool(req.get("diff")), not req.get("no_web"), int(req.get("max", 300)),
                                 req.get("root"), bool(req.get("interactive")))
        if cmd == "find":
            return self.find(req["query"], req.get("role"), req.get("window"), not req.get("no_web"))
        if cmd == "wait":
            return self.wait(req)
        if cmd == "doctor":
            return self.doctor()
        if cmd == "uia":
            return self.uia_dump(req["target"], int(req.get("max", 300)))
        if cmd == "notes":
            return profiles.notes_text(self.active_profile())
        confirm = bool(req.get("confirm"))
        if cmd in ("click", "type", "key"):
            if cmd == "click":
                action = lambda: self.click(req["ref"], req.get("at"), req.get("mode") or "post", confirm)  # noqa: E731
            elif cmd == "type":
                action = lambda: self.type_text(req["ref"], req["text"])  # noqa: E731
            else:
                action = lambda: self.key(req["ref"], req["key"], confirm)  # noqa: E731
            out = self.act_then_diff(req["ref"], action) if req.get("diff") else action()
        elif cmd == "read":
            return self.read(req["ref"])
        elif cmd == "shot":
            return self.shot(req.get("target"), req.get("path"))
        elif cmd == "acc":
            return self.acc(req["target"], int(req.get("max", 300)))
        elif cmd == "js":
            return self.js(req.get("window"), req["expr"], confirm)
        else:
            raise errors.invalid(f"unknown command {cmd}")
        after = winlib.cursor_pos()
        # The agent never moves the cursor itself; a change here is usually the user's own hand,
        # but it is reported so an app that fakes real input gets noticed.
        return out + ("" if before == after else f"  [note: cursor moved {before}->{after} during the action]")


class DeskFail(RuntimeError):
    """Carries the coded error text out of handle(); the serve loop turns it into ok=false."""


def _idle_loop(res: dict) -> None:
    while True:
        time.sleep(IDLE_CHECK)
        try:
            res["agent"].idle_tick()
        except Exception:  # noqa: BLE001
            logging.error("idle tick: %s", traceback.format_exc())


def serve(allow: list[str], prof: dict | None = None) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    winlib.set_dpi_aware()
    agent = Agent(allow, prof)
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(), "started": time.time(),
                   "elevated": winlib.is_elevated(), "allow": sorted(agent.allow)}, f)
    logging.info("listening on %s elevated=%s allow=%s", port, winlib.is_elevated(), agent.allow)
    running = True
    while running:
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(120)
            try:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf.decode("utf-8"))
                if not hmac.compare_digest(str(req.get("token", "")), token):
                    resp = {"ok": False, "out": "bad token"}
                elif req.get("cmd") == "shutdown":
                    resp = {"ok": True, "out": "bye"}
                    running = False
                elif req.get("cmd") == "reload":
                    # Pick up code edits without a new UAC prompt. The serve loop itself is not reloaded.
                    import importlib
                    from . import iedom as _ie, winlib as _wl
                    importlib.reload(_wl)
                    importlib.reload(_ie)  # msaa is reloaded by agent.py itself
                    mod = importlib.reload(importlib.import_module("deskagent.agent"))
                    fresh = mod.Agent.__new__(mod.Agent)
                    fresh.__dict__.update(agent.__dict__)
                    agent = fresh
                    resp = {"ok": True, "out": "reloaded (serve loop changes still need restart)"}
                else:
                    logging.info("cmd %s", {k: v for k, v in req.items() if k not in ("token", "text")})
                    resp = {"ok": True, "out": agent.handle(req)}
            except Exception as e:  # noqa: BLE001
                # Coded errors (DeskFail) are expected outcomes; only unexpected ones get a traceback.
                if type(e).__name__ == "DeskFail" and not str(e).startswith(("INTERNAL", "APP_ERROR")):
                    logging.info("refused: %s", str(e).splitlines()[0])
                    resp = {"ok": False, "out": str(e)}
                else:
                    logging.error("error: %s", traceback.format_exc())
                    resp = {"ok": False, "out": str(e) if type(e).__name__ == "DeskFail" else f"{type(e).__name__}: {e}"}
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", help="profile name (profiles/, local/profiles/); default from local/config.json")
    ap.add_argument("--allow", help="comma-separated process names; overrides the profile's allow list")
    a = ap.parse_args()
    try:
        name = a.profile or profiles.default_name()
        prof = profiles.load(name) if name else {}
        allow = [x.strip() for x in a.allow.split(",") if x.strip()] if a.allow else prof.get("allow", [])
        if not allow:
            raise SystemExit("no apps to drive: pass --profile NAME or --allow a.exe,b.exe")
        serve(allow, prof)
    except Exception:  # noqa: BLE001
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
