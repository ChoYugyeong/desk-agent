"""Error codes with a retry verdict, so a calling agent can branch on the code instead of the message.

Output form (first line): `CODE retry=safe|unsafe|never: message`, then `hint: ...`.
Modelled on agent-desktop's error envelope (code + disposition + suggestion).
"""
from __future__ import annotations


class DeskError(RuntimeError):
    def __init__(self, code: str, message: str, retry: str = "never", hint: str = ""):
        self.code, self.retry, self.hint = code, retry, hint
        text = f"{code} retry={retry}: {message}"
        if hint:
            text += f"\nhint: {hint}"
        super().__init__(text)


def unknown_ref(ref: str) -> DeskError:
    return DeskError("UNKNOWN_REF", f"{ref} is not a ref from a snapshot", "safe",
                     "run `desk snapshot` (or `desk find`) and use a ref it prints")


def stale_ref(ref: str, why: str) -> DeskError:
    return DeskError("STALE_REF", f"{ref}: {why}", "safe", "take a fresh snapshot and use the new ref; nothing was sent")


def window_not_found(sel: str) -> DeskError:
    return DeskError("WINDOW_NOT_FOUND", f"no allowed window matches {sel!r}", "safe", "`desk windows` lists the windows")


def minimized() -> DeskError:
    return DeskError("WINDOW_MINIMIZED", "the window is minimized; it cannot be drawn or clicked by position", "never",
                     "ask the user to restore it (it can stay behind other windows)")


def not_supported(what: str) -> DeskError:
    return DeskError("ACTION_NOT_SUPPORTED", what, "never", "use another command or element; repeating fails the same way")


def timeout(what: str, seconds: float) -> DeskError:
    return DeskError("TIMEOUT", f"{what} not reached within {seconds:g}s", "safe",
                     "the app may still be loading; snapshot to see where it is before retrying")


def invalid(what: str) -> DeskError:
    return DeskError("INVALID_ARGS", what, "never")


def classify(e: Exception) -> str:
    """Text for an unexpected exception, with a code so callers can still branch."""
    if isinstance(e, DeskError):
        return str(e)
    name = type(e).__name__
    if name == "COMError":
        return f"APP_ERROR retry=unsafe: the app rejected the call ({e})\nhint: snapshot to see whether anything changed"
    return f"INTERNAL retry=unsafe: {name}: {e}"
