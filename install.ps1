# desk-agent installer (Windows PowerShell 5.1+). ASCII only on purpose: PS 5.1 misreads BOM-less UTF-8.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1            # venv + dependencies + self-check
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Skill     # also copy the skill to ~/.claude/skills
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mcp       # also register the MCP server in Claude Code (user scope)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Test      # also run the end-to-end tests
param([switch]$Skill, [switch]$Mcp, [switch]$Test)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Find-Python {
    foreach ($c in @('py -3', 'python')) {
        $exe, $arg = $c.Split(' ', 2)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $v = & $exe $arg -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]'3.10') { return @($exe, $arg) }
        }
    }
    throw 'Python 3.10 or newer is required (https://www.python.org/downloads/windows/).'
}

$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    $py = Find-Python
    Write-Host "creating .venv with $($py -join ' ')"
    & $py[0] $py[1] -m venv .venv
}
& $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt
# Generate the comtypes wrappers for MSAA and UI Automation once, and check the package imports.
& $venvPy -c "import deskagent.msaa, deskagent.uia, deskagent.agent; print('deskagent imports ok')"

if ($Skill) {
    $dst = Join-Path $env:USERPROFILE '.claude\skills\desk-agent'
    New-Item -ItemType Directory -Force $dst | Out-Null
    $text = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'skills\desk-agent\SKILL.md')
    $text = $text.Replace('C:\Dev\desk-agent', $root)
    [IO.File]::WriteAllText((Join-Path $dst 'SKILL.md'), $text, (New-Object Text.UTF8Encoding($false)))
    Write-Host "skill installed: $dst"
}

if ($Mcp) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'claude CLI not found' }
    & claude mcp add desk -s user -- $venvPy (Join-Path $root 'desk.py') mcp
}

if ($Test) {
    & $venvPy -m unittest discover -s tests -v
}

Write-Host ''
Write-Host 'next:'
Write-Host '  desk.cmd profiles                 list profiles (profiles\, local\profiles\)'
Write-Host '  desk.cmd start --profile NAME     start the agent (one UAC prompt)'
Write-Host '  desk.cmd doctor                   check everything'
