"""App profiles: which processes the agent may drive, extra policy rules, and notes for AI agents.

Lookup order for a profile NAME:
  local/profiles/NAME.json   (git-ignored: your own apps, company-specific rules and notes)
  profiles/NAME.json         (shipped examples)

Profile file:
  {
    "allow": ["notepad.exe"],              process names the agent may drive
    "confirm_labels": ["..."],             added to policy.json's list
    "confirm_keys": ["f10"],               added to policy.json's list
    "notes": "notes/notepad.md"            optional, relative to the profile file; shown by `desk notes`
  }
local/config.json may name the profile `desk start` uses by default: {"default_profile": "NAME"}.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = (os.path.join(ROOT, "local", "profiles"), os.path.join(ROOT, "profiles"))
LOCAL_CONFIG = os.path.join(ROOT, "local", "config.json")


def path_of(name: str) -> str | None:
    for d in DIRS:
        p = os.path.join(d, name + ".json")
        if os.path.exists(p):
            return p
    return None


def available() -> list[str]:
    names = set()
    for d in DIRS:
        if os.path.isdir(d):
            names.update(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
    return sorted(names)


def default_name() -> str | None:
    try:
        with open(LOCAL_CONFIG, encoding="utf-8") as f:
            return json.load(f).get("default_profile")
    except (OSError, ValueError):
        return None


def load(name: str) -> dict:
    p = path_of(name)
    if not p:
        raise RuntimeError(f"profile {name!r} not found; available: {', '.join(available()) or '(none)'}")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = name
    data["_path"] = p
    if data.get("notes"):
        data["notes"] = os.path.normpath(os.path.join(os.path.dirname(p), data["notes"]))
    return data


def notes_text(profile: dict) -> str:
    p = profile.get("notes")
    if not p:
        return f"(profile {profile.get('name')!r} has no notes)"
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        return f"(notes file {p} unreadable: {e})"
