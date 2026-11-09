---
name: desk-agent
description: Read and operate Windows desktop apps that have no API or MCP, including apps running as administrator, without taking over the user's mouse or screen. Use when the user asks to check, search, read or click something in a desktop exe covered by a desk-agent profile, or when screen-coordinate automation keeps missing because windows move.
---

# desk-agent

Drive the apps in the active profile through a compact element tree with `@eN` refs. Every action goes through
window messages, an embedded IE page's DOM, or accessibility (MSAA, UI Automation) inside the app, so the user's
real mouse and keyboard stay free. Never fall back to screen-coordinate tools (SendInput, pyautogui, computer use)
for these apps.

Interface: the `desk_*` MCP tools if connected, otherwise `C:\Dev\desk-agent\desk.cmd <command>`.
In PowerShell write refs as `e12` or `'@e12'`; a bare `@e12` is PowerShell splatting.
Snapshots, reads and shots can contain private or customer data. Keep them out of commits, public logs and uploads.

## Before the first action

1. `desk doctor`: agent running and elevated, active profile, target app running, minimized windows.
2. `desk notes`: the profile's notes about this app (internal control names, layout, actions to avoid). Follow them.

## Workflow

1. Look for what you need instead of reading everything:
   - one element: `desk find "Save"` or `desk find Search --role button`
   - an overview: `desk snapshot -w w1 --no-web -i` (only lines with refs); drill in with `--root @e170`
2. Act on a ref and see the effect in the same call: `desk click @e12 --diff`, `desk type @e5 "text" --diff`,
   `desk key @e5 enter --diff`. The output ends with the lines that changed after the window settled.
3. Wait with conditions, never with sleeps: `desk wait --text "Done"`, `desk wait --gone "Loading"`,
   `desk wait --title "Details"`, `desk wait --stable`.
4. `desk read @eN` for the full text of an element; for a grid row it returns all cells tab-separated.
5. Refs stay valid across snapshots while the element exists.

Snapshot sections: plain lines are win32 controls, `[web]` an embedded IE page, `[acc]` MSAA content (ribbons,
toolbars, grids; one line per grid row), `[uia]` UI Automation content (WPF, WinUI). Owner-drawn controls often show
internal names instead of their captions; when unsure, look at `desk shot w1` and match by position.
`desk click w1 --at x,y` takes pixel coordinates from that same image.

## Errors

The first line is `CODE retry=safe|unsafe|never: message`, then a `hint:` line. Branch on the code, not the wording.

| Code | Meaning | Do |
|---|---|---|
| `UNKNOWN_REF`, `STALE_REF` | ref not known / element gone or changed; nothing was sent | snapshot or find again, use the new ref |
| `WINDOW_NOT_FOUND` | no allowed window matches | `desk windows` |
| `WINDOW_MINIMIZED` | shot / `--at` impossible | ask the user to restore the window |
| `POLICY_DENIED` | outward action needs the user's approval; nothing was sent | ask; retry with `--confirm` only if they agree |
| `TIMEOUT` | wait condition not reached | snapshot to see the state, then decide |
| `ACTION_NOT_SUPPORTED`, `INVALID_ARGS` | wrong element or arguments | change the command; repeating fails the same way |
| `APP_ERROR`, `INTERNAL` | the app or agent failed mid-action (retry=unsafe) | snapshot to check whether it took effect before anything else |
| `NOT_RUNNING` | agent down | ask the user to run `desk start` |

Retry automatically only when `retry=safe`.

## Rules

- The policy refuses clicks and keys on targets labelled like send, delete, pay (plus the profile's own words) and
  page scripts (`desk js`). On `POLICY_DENIED`, stop and ask the user about that exact action. Repeat with
  `--confirm` (MCP `confirm=true`) only after they agree. Never add `--confirm` pre-emptively.
- Search or filter buttons often run immediately with whatever is in the input boxes. Clear test input before
  changing filters, and put filters back the way you found them.
- When testing a command, use a ref that does not exist (`e999999`), never a guess: a guessed ref may be real.
- The agent runs elevated and is started by the user (`desk start`, one UAC prompt). Do not start it repeatedly.
  After editing agent code, use `desk reload` (no UAC). Do not stop the agent at the end of a task.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `NOT_RUNNING` / `agent is not running` | agent not started or crashed | ask the user to run `desk start --profile NAME` |
| `WINDOW_MINIMIZED` | minimized windows cannot be drawn; some apps also stop laying out content | ask the user to restore it (it may stay behind other windows) |
| element missing from snapshot | collapsed ribbon group, other tab, or not laid out while minimized | open the group / switch tab, then snapshot |
| `(embedded Chromium view: not readable ...)` | CEF view without accessibility | the user does that step |
