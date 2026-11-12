# thousand-eyes one-line installer (Windows / PowerShell)
#
#   powershell -c "irm https://raw.githubusercontent.com/janedoe/thousand-eyes/main/install.ps1 | iex"
#
# What it does:
#   1. finds a python 3.10+ (py launcher, python3, python)
#   2. makes sure pipx is installed (isolated CLI env, no admin needed)
#   3. pipx installs thousand-eyes from this repo
#   4. injects numpy (the animated banner needs it, everything else does not)
#   5. verifies the binary and prints the three next steps
#
# Re-running is safe: pipx reinstalls/updates in place, nothing else is touched.

$ErrorActionPreference = "Continue"   # native tools write to stderr; we check exit codes ourselves

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Get-Python {
    $candidates = @(
        @{ cmd = "py";      args = @("-3") },
        @{ cmd = "python3"; args = @() },
        @{ cmd = "python";  args = @() }
    )
    foreach ($c in $candidates) {
        $exe = Get-Command $c.cmd -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $probe = @($c.args + @("-c", "import sys;print('%d.%d' % sys.version_info[:2])"))
        try {
            $out = & $exe.Source @probe 2>$null
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
        $ver = ("$out").Trim()
        if ($ver -match '^(\d+)\.(\d+)$') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                return @{ exe = $exe.Source; args = $c.args }
            }
        }
    }
    return $null
}

Write-Step "thousand-eyes installer"

$py = Get-Python
if (-not $py) {
    Write-Host "    python 3.10+ not found. Install it from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "    (tick 'Add python.exe to PATH'), then re-run this command." -ForegroundColor Red
    exit 1
}
Write-Ok "python: $($py['exe']) $($py['args'] -join ' ')"

# --- pipx -----------------------------------------------------------------
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if (-not $pipx) {
    Write-Step "installing pipx (isolated CLI environments)"
    & $py['exe'] @($py['args'] + @("-m", "pip", "install", "--user", "--upgrade", "pipx")) 2>&1 | Out-Null
    $pyexeDir = Split-Path -Parent $py.exe
    $scripts = Join-Path $pyexeDir "Scripts"
    if (Test-Path $scripts) {
        $env:PATH = "$scripts;$env:PATH"
    }
    $userScripts = Join-Path $env:APPDATA "Python\Scripts"
    if (Test-Path $userScripts) {
        $env:PATH = "$userScripts;$env:PATH"
    }
    $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if (-not $pipx) {
        $pipxPath = Join-Path $scripts "pipx.exe"
        if (Test-Path $pipxPath) {
            $pipx = @{ Source = $pipxPath }
        }
    }
    if (-not $pipx) {
        Write-Warn2 "pipx installed but not on PATH for this shell - opening a new terminal will fix it."
        exit 0
    }
}
Write-Ok "pipx: $($pipx.Source)"

# --- the tool -------------------------------------------------------------
Write-Step "installing thousand-eyes (from the repo, pinned deps, no admin)"
& $pipx.Source install "git+https://github.com/harshaxyZ/thousand-eyes.git" --force 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    install failed - see the output above." -ForegroundColor Red
    exit 1
}
Write-Ok "thousand-eyes installed"

Write-Step "adding numpy (animated banner only - the audit itself stays dependency-light)"
& $pipx.Source inject thousand-eyes numpy 2>&1 | Out-Null
Write-Ok "numpy injected"

Write-Step "verifying"
$exe = Get-Command thousand-eyes -ErrorAction SilentlyContinue
if (-not $exe) {
    $scripts = Join-Path $env:LOCALAPPDATA "pipx\pipx\venvs\thousand-eyes\Scripts\thousand-eyes.exe"
    if (Test-Path $scripts) { Write-Ok "binary at $scripts" }
    Write-Warn2 "the thousand-eyes command may need a new terminal to appear on PATH"
} else {
    Write-Ok "command ready: $((& $exe.Source --version))"
}

Write-Host ""
Write-Host "  next steps" -ForegroundColor Magenta
Write-Host "    thousand-eyes                  open the chat console (audit + chat)"
Write-Host "    thousand-eyes --setup          add your LLM key (gemini free tier or claude)"
Write-Host "    thousand-eyes --skill install  teach your IDE agent how to drive it"
Write-Host "    thousand-eyes you@email.com    run your first self-audit"
Write-Host ""
Write-Host "  free by design: every data source is a free, keyless, first-party public endpoint." -ForegroundColor DarkGray
Write-Host "  nothing leaves your machine except read-only GETs to the public web." -ForegroundColor DarkGray
Write-Host ""
