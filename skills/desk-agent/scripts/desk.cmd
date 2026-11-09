@echo off
setlocal
rem desk-agent launcher shipped with the skill.
rem Engine folder: DESK_AGENT_HOME, else home.txt next to this file (written by install.ps1 -Skill),
rem else the repo this skill sits in, else %LOCALAPPDATA%\desk-agent\app (installed on first use).
if not defined DESK_AGENT_HOME if exist "%~dp0home.txt" set /p DESK_AGENT_HOME=<"%~dp0home.txt"
if not defined DESK_AGENT_HOME if exist "%~dp0..\..\..\desk.py" for %%I in ("%~dp0..\..\..") do set "DESK_AGENT_HOME=%%~fI"
if not defined DESK_AGENT_HOME set "DESK_AGENT_HOME=%LOCALAPPDATA%\desk-agent\app"
set "SETUP=%~dp0bootstrap.ps1"
if exist "%DESK_AGENT_HOME%\install.ps1" set "SETUP=%DESK_AGENT_HOME%\install.ps1"
if not exist "%DESK_AGENT_HOME%\.venv\Scripts\python.exe" (
  echo desk-agent: first run, installing into %DESK_AGENT_HOME% 1>&2
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%" 1>&2
  if errorlevel 1 exit /b 1
)
"%DESK_AGENT_HOME%\.venv\Scripts\python.exe" "%DESK_AGENT_HOME%\desk.py" %*
