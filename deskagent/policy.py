"""Action policy: actions with outside effects (send, delete, pay ...) need an explicit confirm.

The check runs inside the elevated agent, so it holds no matter which client (CLI, MCP, script) sends the
command, the same way a sandbox enforces its egress policy outside the workload.

Policy file: %LOCALAPPDATA%\\desk-agent\\policy.json if present, else policy.json in the project root.
The active profile's confirm_labels / confirm_keys are added on top (see profiles.py).
"""
from __future__ import annotations

import json
import os

from .errors import DeskError

PROJECT_POLICY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "policy.json")
USER_POLICY = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "desk-agent", "policy.json")

DEFAULT = {
    "confirm_labels": ["발송", "전송", "삭제", "결제", "송금", "이체", "send", "delete", "submit", "pay", "transfer"],
    "confirm_keys": [],
    "confirm_js": True,
    "confirm_at_clicks": False,
}

# Set by the agent from the active profile before each command.
EXTRA: dict = {}


def load() -> dict:
    base = None
    for path in (USER_POLICY, PROJECT_POLICY):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            base = {**DEFAULT, **data, "_path": path}
            break
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            # A broken policy file must not silently turn the guard off.
            raise RuntimeError(f"policy file {path} is unreadable: {e}") from e
    if base is None:
        base = {**DEFAULT, "_path": "(built-in default)"}
    for k in ("confirm_labels", "confirm_keys"):
        base[k] = list(base.get(k, [])) + [x for x in EXTRA.get(k, []) if x not in base.get(k, [])]
    if EXTRA.get("name"):
        base["_path"] += f" + profile {EXTRA['name']}"
    return base


HINT = "ask the user about this exact action; only if they agree, repeat it with --confirm (MCP: confirm=true)"


class Blocked(DeskError):
    def __init__(self, message: str):
        super().__init__("POLICY_DENIED", message + " Nothing was sent.", "never", HINT)


def _hint() -> str:
    return ""


def check_label(label: str, action: str, confirm: bool) -> None:
    if confirm or not label:
        return
    pol = load()
    low = label.lower()
    for pat in pol.get("confirm_labels", []):
        if pat and pat.lower() in low:
            raise Blocked(f'{action} on "{label}" matches "{pat}".')


def check_key(key: str, confirm: bool) -> None:
    if confirm:
        return
    if key.lower() in [k.lower() for k in load().get("confirm_keys", [])]:
        raise Blocked(f'key "{key}" is listed in confirm_keys.')


def check_flag(flag: str, what: str, confirm: bool) -> None:
    if confirm:
        return
    if load().get(flag):
        raise Blocked(f"{what} needs confirmation ({flag}).")
