# Notepad notes (example profile)

- Windows 11 Notepad is a WinUI app: menus and tabs come through the UI Automation layer (`[uia]` in snapshots),
  the text area is a RichEdit window (`edit = "..."`, typed with WM_SETTEXT).
- Starting `notepad.exe` while Notepad is open adds a tab to the existing window, and unsaved tabs are restored on
  the next start. Do not type into the user's documents unless asked; prefer a new tab the user opened for the task.
- Saving shows a dialog (`[wN] dialog`); read it with a snapshot before pressing anything.

Copy this file and `../notepad.json` to `local/profiles/` to make your own profile for another app.
