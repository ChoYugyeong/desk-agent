"""MSAA (IAccessible) layer for owner-drawn controls (ribbons, toolbars, grids) that have no child windows.

Elements are addressed by a child-index path from a window's OBJID_CLIENT object, e.g. (2, 0, 5).
Actions use accDoDefaultAction / accSelect, which run inside the target app without the real mouse.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

import comtypes
import comtypes.client
from comtypes import GUID
from comtypes.automation import IDispatch, VARIANT, VT_DISPATCH, VT_I4

from .errors import DeskError

comtypes.client.GetModule("oleacc.dll")
from comtypes.gen.Accessibility import IAccessible  # noqa: E402

oleacc = ctypes.oledll.oleacc
oleacc.AccessibleObjectFromWindow.argtypes = [wt.HWND, wt.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.POINTER(IAccessible))]
oleacc.AccessibleChildren.argtypes = [ctypes.POINTER(IAccessible), ctypes.c_long, ctypes.c_long, ctypes.POINTER(VARIANT), ctypes.POINTER(ctypes.c_long)]
_GetRoleText = ctypes.windll.oleacc.GetRoleTextW
_GetRoleText.argtypes = [wt.DWORD, wt.LPWSTR, wt.UINT]

OBJID_CLIENT = 0xFFFFFFFC
SELF = 0
STATE_UNAVAILABLE = 0x1
STATE_SELECTED = 0x2
STATE_FOCUSED = 0x4
STATE_PRESSED = 0x8
STATE_CHECKED = 0x10
STATE_INVISIBLE = 0x8000
STATE_OFFSCREEN = 0x10000
SELFLAG_TAKEFOCUS = 0x1
SELFLAG_TAKESELECTION = 0x2

ROLE_NAMES = {1: "titlebar", 2: "menubar", 3: "scrollbar", 4: "grip", 9: "window", 10: "client", 11: "menupopup",
              12: "menuitem", 13: "tooltip", 15: "document", 16: "pane", 20: "grouping", 21: "separator",
              22: "toolbar", 23: "statusbar", 24: "table", 25: "columnheader", 26: "rowheader", 28: "row",
              29: "cell", 30: "link", 33: "list", 34: "listitem", 35: "outline", 36: "outlineitem",
              37: "tab", 38: "propertypage", 40: "graphic", 41: "text", 42: "edit", 43: "button",
              44: "checkbox", 45: "radio", 46: "combobox", 47: "droplist", 56: "splitbutton",
              57: "buttonmenu", 58: "buttondropdowngrid", 60: "tablist", 48: "progressbar", 51: "slider", 52: "spinbutton", 62: "buttondropdown"}
# Roles that are only structure; they are walked through but not given refs unless named.
STRUCTURE_ROLES = {2, 9, 10, 16, 20, 22, 38, 60}
NOISE_ROLES = {1, 3, 4}


class Node:
    """An accessible element: either a full IAccessible object or a simple child (acc, child_id)."""

    def __init__(self, acc, child: int = SELF):
        self.acc = acc
        self.child = child
        self._c: dict[str, object] = {}  # every property is a cross-process call; ask each one once

    def _get(self, prop: str, kind: type, default):
        if prop not in self._c:
            v = _call(self.acc, prop, self.child)
            self._c[prop] = v if isinstance(v, kind) else default
        return self._c[prop]

    @property
    def name(self) -> str:
        return self._get("accName", str, "")

    @property
    def value(self) -> str:
        return self._get("accValue", str, "")

    @property
    def default_action(self) -> str:
        return self._get("accDefaultAction", str, "")

    @property
    def role_id(self) -> int:
        return self._get("accRole", int, 0)

    @property
    def role(self) -> str:
        r = self.role_id
        if r in ROLE_NAMES:
            return ROLE_NAMES[r]
        buf = ctypes.create_unicode_buffer(64)
        _GetRoleText(r, buf, 64)
        return buf.value or f"role{r}"

    @property
    def state(self) -> int:
        return self._get("accState", int, 0)

    def location(self) -> tuple[int, int, int, int] | None:
        try:
            l, t, w, h = self.acc.accLocation(self.child)
            return l, t, w, h
        except (comtypes.COMError, OSError, ValueError, TypeError):
            return None

    def children(self) -> list["Node"]:
        if self.child != SELF:
            return []
        return list_children(self.acc)

    def do_default(self) -> None:
        self._c.clear()
        self.acc.accDoDefaultAction(self.child)

    def set_value(self, text: str) -> None:
        self._c.clear()
        self.acc._IAccessible__com__set_accValue(VARIANT(self.child), text)

    def select(self, flags: int = SELFLAG_TAKEFOCUS | SELFLAG_TAKESELECTION) -> None:
        self._c.clear()
        self.acc.accSelect(flags, self.child)


def _call(acc, prop, child):
    try:
        return getattr(acc, prop)(child)
    except (comtypes.COMError, OSError, ValueError, TypeError):
        return None


def _call_str(acc, prop, child) -> str | None:
    v = _call(acc, prop, child)
    return v if isinstance(v, str) else None


def from_window(hwnd: int, objid: int = OBJID_CLIENT):
    p = ctypes.POINTER(IAccessible)()
    oleacc.AccessibleObjectFromWindow(hwnd, objid, ctypes.byref(IAccessible._iid_), ctypes.byref(p))
    return p


def list_children(acc) -> list[Node]:
    try:
        n = acc.accChildCount
    except comtypes.COMError:
        return []
    if not n:
        return []
    arr = (VARIANT * n)()
    got = ctypes.c_long()
    try:
        oleacc.AccessibleChildren(acc, 0, n, arr, ctypes.byref(got))
    except OSError:
        return []
    out: list[Node] = []
    for i in range(got.value):
        v = arr[i]
        if v.vt == VT_DISPATCH:
            raw = v._.c_void_p
            if not raw:
                continue
            disp = ctypes.cast(raw, ctypes.POINTER(IDispatch))
            try:
                out.append(Node(disp.QueryInterface(IAccessible)))
            except comtypes.COMError:
                continue
        elif v.vt == VT_I4:
            out.append(Node(acc, v._.VT_I4))
    return out


def resolve(hwnd: int, path: tuple[int, ...]) -> Node:
    node = Node(from_window(hwnd))
    for i in path:
        kids = node.children()
        if i >= len(kids):
            raise DeskError("STALE_REF", "the element is gone", "safe", "take a fresh snapshot and use the new ref")
        node = kids[i]
    return node


ROW_ROLES = {28}  # table rows are printed as one line; their cells are read in one go


def walk(hwnd: int, max_items: int = 300, max_depth: int = 12, skip_windows: bool = False, collapse=ROW_ROLES):
    """Yield (depth, path, node) for visible elements, depth-first.

    skip_windows leaves out child-window objects (role window), which the win32 layer lists itself.
    """
    root = Node(from_window(hwnd))
    count = 0
    stack = [(0, (), root)]
    while stack and count < max_items:
        depth, path, node = stack.pop()
        if path:
            st = node.state
            rid = node.role_id
            if st & STATE_INVISIBLE or rid in NOISE_ROLES or (rid == 0 and not node.name):
                continue
            if skip_windows and rid == 9:
                continue
            count += 1
            yield depth, path, node
        if depth >= max_depth or (path and node.role_id in collapse):
            continue
        kids = node.children()
        for i in range(len(kids) - 1, -1, -1):
            stack.append((depth + 1, path + (i,), kids[i]))


def describe(node: Node) -> tuple[str, str, list[str]]:
    role = node.role
    name = node.name.replace("\r", " ").replace("\n", " ").strip()
    flags = []
    st = node.state
    if st & STATE_UNAVAILABLE:
        flags.append("disabled")
    if st & STATE_SELECTED:
        flags.append("selected")
    if st & (STATE_CHECKED | STATE_PRESSED):
        flags.append("checked")
    if st & STATE_OFFSCREEN:
        flags.append("offscreen")
    return role, name, flags


class Columns:
    """Column layout of a grid, read once from its first row: cell count and hidden column indexes."""

    def __init__(self, row: Node):
        kids = row.children()
        self.count = len(kids)
        self.hidden = {i for i, c in enumerate(kids) if c.state & STATE_INVISIBLE}


def row_cells(node: Node, cols: Columns | None = None) -> list[str]:
    """Values of the cells in a table row, hidden columns left out.

    Grids such as C1 FlexGrid return the whole row tab-separated from the row's accValue: one call
    instead of one per cell (82 cells in a real order list). Falls back to per-cell reads otherwise.
    """
    hidden = cols.hidden if cols else set()
    v = node.value
    parts = v.split("\t") if v else []
    if cols and parts and len(parts) == cols.count:
        vals = parts
    else:
        vals = [c.value for c in node.children()]
    return [" ".join(x.split()) for i, x in enumerate(vals) if i not in hidden]
