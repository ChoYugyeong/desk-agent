"""UI Automation layer for apps without child windows or MSAA content (WPF, WinUI/XAML, UWP).

One BuildCache call fetches the whole control-view subtree with the properties below, so a snapshot
costs a single cross-process round trip per window. Actions use UIA patterns inside the app
(Invoke, Toggle, SelectionItem, ExpandCollapse, Value, LegacyIAccessible), never the real mouse.
"""
from __future__ import annotations

import comtypes
import comtypes.client as cc

from .errors import DeskError

cc.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as U  # noqa: E402

P_RUNTIME, P_CTYPE, P_NAME, P_ENABLED, P_AUTOID, P_CLASS = 30000, 30003, 30005, 30010, 30011, 30012
P_PASSWORD, P_HWND, P_OFFSCREEN = 30019, 30020, 30022
P_HAS_EXPAND, P_HAS_INVOKE, P_HAS_SELITEM, P_HAS_TOGGLE, P_HAS_VALUE = 30028, 30031, 30036, 30041, 30043
P_VALUE, P_EXPSTATE, P_SELECTED, P_TOGGLE = 30045, 30070, 30079, 30086
PROPS = [P_RUNTIME, P_CTYPE, P_NAME, P_ENABLED, P_AUTOID, P_CLASS, P_PASSWORD, P_HWND, P_OFFSCREEN, P_HAS_EXPAND,
         P_HAS_INVOKE, P_HAS_SELITEM, P_HAS_TOGGLE, P_HAS_VALUE, P_VALUE, P_EXPSTATE, P_SELECTED, P_TOGGLE]

TYPES = {50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox", 50004: "edit", 50005: "link",
         50006: "image", 50007: "listitem", 50008: "list", 50009: "menu", 50010: "menubar", 50011: "menuitem",
         50012: "progressbar", 50013: "radio", 50014: "scrollbar", 50015: "slider", 50016: "spinner",
         50017: "statusbar", 50018: "tablist", 50019: "tab", 50020: "text", 50021: "toolbar", 50022: "tooltip",
         50023: "tree", 50024: "treeitem", 50025: "custom", 50026: "group", 50027: "thumb", 50028: "grid",
         50029: "dataitem", 50030: "document", 50031: "splitbutton", 50032: "window", 50033: "pane",
         50034: "header", 50035: "headeritem", 50036: "table", 50037: "titlebar", 50038: "separator",
         50039: "semanticzoom", 50040: "appbar"}
STRUCTURE = {"pane", "group", "custom", "window", "toolbar", "menubar", "tablist", "list", "tree", "grid",
             "table", "header", "document", "appbar", "semanticzoom", "statusbar"}
NOISE = {"scrollbar", "thumb", "titlebar", "separator", "tooltip", "image"}

# Window classes that host XAML/WinUI content: their insides are only visible through UIA.
XAML_HOSTS = ("Windows.UI.Composition.DesktopWindowContentBridge", "Microsoft.UI.Content.DesktopChildSiteBridge",
              "Microsoft.UI.Content.ContentWindowSiteBridge", "DesktopWindowXamlSource", "Windows.UI.Core.CoreWindow",
              "ApplicationFrameInputSinkWindow")

_uia = None
_cache = None


def client():
    global _uia, _cache
    if _uia is None:
        _uia = cc.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        _cache = _uia.CreateCacheRequest()
        for p in PROPS:
            _cache.AddProperty(p)
        _cache.TreeScope = U.TreeScope_Subtree
        _cache.TreeFilter = _uia.ControlViewCondition
    return _uia, _cache


class Node:
    def __init__(self, el):
        self.el = el

    def get(self, pid, default=None):
        try:
            v = self.el.GetCachedPropertyValue(pid)
        except comtypes.COMError:
            return default
        return default if v is None else v

    @property
    def role(self) -> str:
        return TYPES.get(self.get(P_CTYPE, 0), "element")

    @property
    def name(self) -> str:
        return " ".join(str(self.get(P_NAME, "")).split())

    @property
    def runtime_id(self) -> tuple:
        v = self.get(P_RUNTIME, ())
        try:
            return tuple(v)
        except TypeError:
            return (v,)

    def children(self) -> list["Node"]:
        try:
            arr = self.el.GetCachedChildren()
        except comtypes.COMError:
            return []
        if not arr:
            return []
        return [Node(arr.GetElement(i)) for i in range(arr.Length)]

    def actions(self) -> list[str]:
        acts = []
        if self.get(P_HAS_INVOKE):
            acts.append("invoke")
        if self.get(P_HAS_TOGGLE):
            acts.append("toggle")
        if self.get(P_HAS_SELITEM):
            acts.append("select")
        if self.get(P_HAS_EXPAND):
            acts.append("expand")
        if self.get(P_HAS_VALUE):
            acts.append("value")
        return acts

    def flags(self) -> list[str]:
        f = []
        if not self.get(P_ENABLED, True):
            f.append("disabled")
        if self.get(P_HAS_TOGGLE) and self.get(P_TOGGLE, 0) == 1:
            f.append("checked")
        if self.get(P_HAS_SELITEM) and self.get(P_SELECTED):
            f.append("selected")
        if self.get(P_HAS_EXPAND):
            f.append({0: "collapsed", 1: "expanded", 2: "partial"}.get(self.get(P_EXPSTATE, 3), ""))
        return [x for x in f if x]

    def value(self) -> str | None:
        if not self.get(P_HAS_VALUE):
            return None
        if self.get(P_PASSWORD):
            return "***"
        return " ".join(str(self.get(P_VALUE, "")).split())


def root(hwnd: int) -> Node:
    uia, cache = client()
    try:
        return Node(uia.ElementFromHandleBuildCache(hwnd, cache))
    except comtypes.COMError as e:
        raise DeskError("APP_ERROR", f"UI Automation cannot read this window ({e})", "unsafe") from None


def walk(hwnd: int, max_items: int = 300, skip_child_windows: bool = False):
    """Yield (depth, path, node) depth-first over the cached control view, skipping noise and offscreen items."""
    top = root(hwnd)
    count = 0
    stack = [(0, (), top)]
    while stack and count < max_items:
        depth, path, node = stack.pop()
        if path:
            if node.role in NOISE or node.get(P_OFFSCREEN):
                continue
            h = node.get(P_HWND, 0)
            if skip_child_windows and h and h != hwnd:
                continue  # a real child window: the win32 layer lists it itself
            count += 1
            yield depth, path, node
        kids = node.children()
        for i in range(len(kids) - 1, -1, -1):
            stack.append((depth + 1, path + (i,), kids[i]))


def find(hwnd: int, runtime_id: tuple) -> Node:
    for _, _, node in walk(hwnd, 100000):
        if node.runtime_id == runtime_id:
            return node
    raise DeskError("STALE_REF", "the element is gone", "safe", "take a fresh snapshot and use the new ref")


def _pattern(node: Node, pid: int, iface):
    try:
        p = node.el.GetCurrentPattern(pid)
    except comtypes.COMError:
        return None
    return p.QueryInterface(iface) if p else None


def do_default(node: Node) -> str:
    """Click equivalent: Invoke, else Toggle, else Select, else Expand/Collapse, else MSAA default action."""
    p = _pattern(node, U.UIA_InvokePatternId, U.IUIAutomationInvokePattern)
    if p:
        p.Invoke()
        return "Invoke"
    p = _pattern(node, U.UIA_TogglePatternId, U.IUIAutomationTogglePattern)
    if p:
        p.Toggle()
        return "Toggle"
    p = _pattern(node, U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern)
    if p:
        p.Select()
        return "Select"
    p = _pattern(node, U.UIA_ExpandCollapsePatternId, U.IUIAutomationExpandCollapsePattern)
    if p:
        if node.get(P_EXPSTATE, 0) == 1:
            p.Collapse()
            return "Collapse"
        p.Expand()
        return "Expand"
    p = _pattern(node, U.UIA_LegacyIAccessiblePatternId, U.IUIAutomationLegacyIAccessiblePattern)
    if p:
        p.DoDefaultAction()
        return "LegacyIAccessible.DoDefaultAction"
    raise DeskError("ACTION_NOT_SUPPORTED", f"{node.role} {node.name!r} has no clickable pattern", "never",
                    "use another element, or --at x,y on the window")


def set_value(node: Node, text: str) -> str:
    p = _pattern(node, U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
    if not p:
        raise DeskError("ACTION_NOT_SUPPORTED", f"{node.role} {node.name!r} has no Value pattern", "never")
    p.SetValue(text)
    return "ValuePattern.SetValue"


def read(node: Node) -> str:
    v = node.value()
    return v if v is not None else node.name
