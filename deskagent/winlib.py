"""Win32 helpers: window enumeration, message-based input, per-window capture.

Rule: nothing in this module touches the real mouse or keyboard. There is no
SendInput, mouse_event, keybd_event or SetCursorPos here, on purpose.
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HWND = wt.HWND
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, HWND, LPARAM)

user32.EnumWindows.argtypes = [EnumProc, LPARAM]
user32.EnumChildWindows.argtypes = [HWND, EnumProc, LPARAM]
user32.GetClassNameW.argtypes = [HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindowEnabled.argtypes = [HWND]
user32.IsWindow.argtypes = [HWND]
user32.GetParent.argtypes = [HWND]
user32.GetParent.restype = HWND
user32.GetAncestor.argtypes = [HWND, ctypes.c_uint]
user32.GetAncestor.restype = HWND
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(wt.RECT)]
user32.ScreenToClient.argtypes = [HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SendMessageTimeoutW.argtypes = [HWND, ctypes.c_uint, WPARAM, LPARAM, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = LRESULT
user32.PostMessageW.argtypes = [HWND, ctypes.c_uint, WPARAM, LPARAM]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = ctypes.c_uint
user32.ChildWindowFromPointEx.argtypes = [HWND, wt.POINT, ctypes.c_uint]
user32.ChildWindowFromPointEx.restype = HWND
user32.MapWindowPoints.argtypes = [HWND, HWND, ctypes.POINTER(wt.POINT), ctypes.c_uint]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.PrintWindow.argtypes = [HWND, wt.HDC, ctypes.c_uint]
user32.GetDC.argtypes = [HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [HWND, wt.HDC]
user32.IsIconic.argtypes = [HWND]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETTEXT = 0x000C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
SMTO_ABORTIFHUNG = 0x0002
GWL_STYLE = -16
GA_ROOT = 2
CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPTRANSPARENT = 0x0004
PW_RENDERFULLCONTENT = 0x00000002
MK_LBUTTON = 0x0001

VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
      "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25,
      "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "f5": 0x74}


def set_dpi_aware() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def hval(h) -> int:
    return int(h or 0)


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def get_text(hwnd, limit: int = 2000) -> str:
    """WM_GETTEXT works across processes for controls (GetWindowText does not)."""
    n = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(hwnd, WM_GETTEXTLENGTH, 0, 0, SMTO_ABORTIFHUNG, 500, ctypes.byref(n)):
        return ""
    length = min(int(n.value), limit)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    res = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, length + 1, ctypes.cast(buf, ctypes.c_void_p).value, SMTO_ABORTIFHUNG, 500, ctypes.byref(res))
    return buf.value


def set_text(hwnd, text: str) -> bool:
    buf = ctypes.create_unicode_buffer(text)
    res = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value, SMTO_ABORTIFHUNG, 2000, ctypes.byref(res)))


def send_get(hwnd, msg, wparam=0, lparam=0, timeout=500) -> int | None:
    res = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, timeout, ctypes.byref(res)):
        return None
    return int(res.value)


def rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def client_size(hwnd) -> tuple[int, int]:
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def style(hwnd) -> int:
    return user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def visible(hwnd) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def enabled(hwnd) -> bool:
    return bool(user32.IsWindowEnabled(hwnd))


def top_windows(pids: set[int]) -> list[int]:
    out: list[int] = []

    def cb(h, _):
        if window_pid(h) in pids and user32.IsWindowVisible(h):
            out.append(hval(h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def child_windows(hwnd) -> list[int]:
    out: list[int] = []

    def cb(h, _):
        out.append(hval(h))
        return True

    user32.EnumChildWindows(hwnd, EnumProc(cb), 0)
    return out


def parent(hwnd) -> int:
    return hval(user32.GetParent(hwnd))


def root(hwnd) -> int:
    return hval(user32.GetAncestor(hwnd, GA_ROOT))


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def window_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """Window-relative pixel (as in a PrintWindow capture) -> client coords of the same window."""
    l, t, _, _ = rect(hwnd)
    p = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return x - (p.x - l), y - (p.y - t)


def _mk(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def deepest_child_at(hwnd: int, x: int, y: int) -> tuple[int, int, int]:
    """Walk down from hwnd to the deepest visible child under client point (x, y)."""
    cur, cx, cy = hwnd, x, y
    for _ in range(32):
        child = hval(user32.ChildWindowFromPointEx(cur, wt.POINT(cx, cy), CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT))
        if not child or child == cur:
            break
        pt = wt.POINT(cx, cy)
        user32.MapWindowPoints(cur, child, ctypes.byref(pt), 1)
        cur, cx, cy = child, pt.x, pt.y
    return cur, cx, cy


def message_click(hwnd: int, x: int, y: int, descend: bool = True, mode: str = "post") -> tuple[int, int, int]:
    """Click at client coords of hwnd with mouse messages. The real cursor never moves.

    mode: "post" = posted messages with short gaps (default, never blocks);
          "send" = synchronous with timeout (some owner-drawn controls need the hover to be
                   processed before the button press).
    """
    import time
    target, tx, ty = deepest_child_at(hwnd, x, y) if descend else (hwnd, x, y)
    lp = _mk(tx, ty)
    seq = [(WM_MOUSEMOVE, 0), (WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0)]
    for msg, wp in seq:
        if mode == "send":
            send_get(target, msg, wp, lp, timeout=1500)
        else:
            user32.PostMessageW(target, msg, wp, lp)
        time.sleep(0.06)
    return target, tx, ty


def button_click(hwnd: int) -> None:
    # Posted, not sent: a click that opens a modal dialog must not block the agent.
    user32.PostMessageW(hwnd, BM_CLICK, 0, 0)


def post_key(hwnd: int, key: str) -> None:
    vk = VK.get(key.lower())
    if vk is None:
        if len(key) == 1:
            user32.PostMessageW(hwnd, WM_CHAR, ord(key), 1)
            return
        raise ValueError(f"unknown key: {key}")
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1)
    if vk in (0x0D, 0x09, 0x1B, 0x20, 0x08):
        user32.PostMessageW(hwnd, WM_CHAR, vk, 1)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001)


kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def process_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(h)


def all_top_windows() -> list[int]:
    out: list[int] = []

    def cb(h, _):
        if user32.IsWindowVisible(h):
            out.append(hval(h))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------- own resource use ----------

PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
PROCESS_MODE_BACKGROUND_END = 0x00200000
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
_psapi = ctypes.WinDLL("psapi")
_psapi.EmptyWorkingSet.argtypes = [wt.HANDLE]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def set_background(on: bool) -> None:
    """Background mode lowers this process's CPU, disk and memory priority (Windows trims it first)."""
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(),
                              PROCESS_MODE_BACKGROUND_BEGIN if on else PROCESS_MODE_BACKGROUND_END)


def trim_memory() -> None:
    """Hand this process's RAM pages back to Windows; they come back from the page file on next use."""
    _psapi.EmptyWorkingSet(kernel32.GetCurrentProcess())


def memory_mb() -> tuple[float, float]:
    """(working set in RAM, committed private bytes) in MB."""
    _psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
    c = PROCESS_MEMORY_COUNTERS()
    c.cb = ctypes.sizeof(c)
    _psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 2**20, c.PagefileUsage / 2**20


# ---------- capture ----------

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture_png(hwnd: int, path: str, crop: tuple[int, int, int, int] | None = None) -> tuple[int, int]:
    """Render one window with PrintWindow (works when covered by other windows) and save PNG."""
    if user32.IsIconic(hwnd):
        raise RuntimeError("window is minimized; restore it (it can stay behind other windows)")
    l, t, r, b = rect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise RuntimeError("window has no size")
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            user32.PrintWindow(hwnd, mdc, 0)
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)
    data = buf.raw
    x0, y0, x1, y1 = (0, 0, w, h) if crop is None else crop
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    cw, ch = x1 - x0, y1 - y0
    rows = []
    for y in range(y0, y1):
        row = data[(y * w + x0) * 4:(y * w + x1) * 4]
        rgb = bytearray(cw * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        rows.append(b"\x00" + bytes(rgb))
    raw = b"".join(rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cw, ch, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return cw, ch
