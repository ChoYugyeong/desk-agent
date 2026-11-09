# desk-agent bootstrap: download (or update) the engine and install it. ASCII only (PS 5.1 reads BOM-less files as ANSI).
#
# One line, also installs the skill to ~/.claude/skills:
#   irm https://raw.githubusercontent.com/ChoYugyeong/desk-agent/main/skills/desk-agent/scripts/bootstrap.ps1 | iex
#
# The skill's scripts\desk.cmd runs this file by itself the first time it is used.
# Running it again updates the code and keeps .venv and local\ (your profiles).
#
# Environment overrides:
#   DESK_AGENT_HOME    install folder        (default %LOCALAPPDATA%\desk-agent\app)
#   DESK_AGENT_REF     branch or tag         (default main)
#   DESK_AGENT_SOURCE  copy from this folder instead of downloading (for testing)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$repo = 'ChoYugyeong/desk-agent'
$home_ = $env:DESK_AGENT_HOME
if (-not $home_) { $home_ = Join-Path $env:LOCALAPPDATA 'desk-agent\app' }
$ref = $env:DESK_AGENT_REF
if (-not $ref) { $ref = 'main' }
$viaIex = -not $PSCommandPath

$stage = "$home_.staging"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null
try {
    if ($env:DESK_AGENT_SOURCE) {
        Write-Host "copying from $($env:DESK_AGENT_SOURCE)"
        $src = Join-Path $stage 'src'
        New-Item -ItemType Directory -Force $src | Out-Null
        Get-ChildItem -LiteralPath $env:DESK_AGENT_SOURCE -Force |
            Where-Object { $_.Name -notin @('.git', '.venv', 'local', '__pycache__') } |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $src -Recurse -Force }
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $stage 'src.zip'
        $url = "https://codeload.github.com/$repo/zip/$ref"
        Write-Host "downloading $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $stage 'x') -Force
        $src = (Get-ChildItem -LiteralPath (Join-Path $stage 'x') -Directory | Select-Object -First 1).FullName
    }
    if (-not (Test-Path (Join-Path $src 'desk.py'))) { throw "not a desk-agent source tree: $src" }

    New-Item -ItemType Directory -Force $home_ | Out-Null
    # Code files are replaced; .venv and local\ are not in the source, so they survive updates.
    Get-ChildItem -LiteralPath $src -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $home_ -Recurse -Force
    }
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}

$installArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $home_ 'install.ps1'))
if ($viaIex) { $installArgs += '-Skill' }
& powershell @installArgs
if ($LASTEXITCODE) {
    throw "install failed (exit $LASTEXITCODE). Python 3.10+ is required: winget install Python.Python.3.12"
}
Write-Host "desk-agent installed: $home_"
