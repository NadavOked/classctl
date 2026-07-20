# ClassCtl - one-command installer.
# Easiest: run install.bat (it elevates for you).
# Manual:  powershell -ExecutionPolicy Bypass -File install.ps1
param(
    [string]$InstallDir = "C:\ProgramData\ClassCtl",
    [string]$AgentExe   = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

function Is-Admin {
    return ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# elevate if needed
if (-not (Is-Admin)) {
    Write-Host "Requesting administrator rights..." -ForegroundColor Yellow
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -InstallDir `"$InstallDir`""
    if ($AgentExe) { $a += " -AgentExe `"$AgentExe`"" }
    Start-Process powershell -ArgumentList $a -Verb RunAs
    exit
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "===== ClassCtl Installer =====" -ForegroundColor Cyan

# ---------- 1) Python / exe ----------
function Find-RealPython {
    # returns full path to a real python.exe, skipping the Microsoft Store alias
    foreach ($c in (Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        $p = $c.Source
        if ($p -and (Test-Path $p) -and ($p -notlike "*\WindowsApps\*")) { return $p }
    }
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "C:\Python312\python.exe","C:\Python311\python.exe")) {
        if (Test-Path $p) { return $p }
    }
    try {
        $r = (& py -3 -c "import sys;print(sys.executable)" 2>$null)
        if ($r -and (Test-Path $r)) { return $r }
    } catch { }
    return $null
}

$useExe = ($AgentExe -ne "")
$PyExe = $null
if (-not $useExe) {
    $PyExe = Find-RealPython
    if (-not $PyExe) {
        Write-Host "`nPython is not installed on this computer." -ForegroundColor Yellow
        $ans = Read-Host "Install automatically via winget? (Y = yes / N = I will install manually and re-run)"
        if ($ans -match '^[Yy]') {
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                Write-Host "Installing Python via winget..." -ForegroundColor Cyan
                winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
                $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                            [Environment]::GetEnvironmentVariable("Path","User")
                $PyExe = Find-RealPython
                if (-not $PyExe) {
                    Write-Warning "Python installed, but PATH updates only in a new window. Close this and run install.bat again."
                    Read-Host "Press Enter to exit"; exit
                }
            } else {
                Write-Warning "winget not available. Install Python from https://python.org (check 'Add to PATH') and run again."
                Read-Host "Press Enter to exit"; exit
            }
        } else {
            Write-Host "Install Python from https://python.org (check 'Add python to PATH'), then run install.bat again." -ForegroundColor Yellow
            Read-Host "Press Enter to exit"; exit
        }
    }
    Write-Host "[OK] Python found: $PyExe" -ForegroundColor Green
}

# ---------- 2) copy files ----------
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item (Join-Path $ScriptDir "*.py")            $InstallDir -Force
Copy-Item (Join-Path $ScriptDir "uninstall.ps1")   $InstallDir -Force
Copy-Item (Join-Path $ScriptDir "install_agent.ps1") $InstallDir -Force
if (Test-Path (Join-Path $ScriptDir "classctl.ico")) {
    Copy-Item (Join-Path $ScriptDir "classctl.ico") $InstallDir -Force
}
Write-Host "[OK] Files copied to $InstallDir" -ForegroundColor Green

# hide the folder as well as locking it
try {
    $dirItem = Get-Item $InstallDir -Force
    $dirItem.Attributes = $dirItem.Attributes -bor [IO.FileAttributes]::Hidden
    Write-Host "[OK] Folder hidden" -ForegroundColor Green
} catch { }

# ---------- 3) setup wizard (only if there is no config) ----------
$controllerCfg = Join-Path $InstallDir "controller.json"
if (-not (Test-Path $controllerCfg)) {
    Write-Host "`nOpening setup wizard (password + folder + basic scripts)..." -ForegroundColor Cyan
    if ($useExe) {
        $setupExe = Join-Path $ScriptDir "classctl-setup.exe"
        $proc = Start-Process $setupExe -Wait -PassThru
    } else {
        $wizard = Join-Path $InstallDir "setup_wizard.py"
        # -NoNewWindow: keep output in this console so errors are visible
        $proc = Start-Process -FilePath $PyExe -ArgumentList @($wizard) `
                    -WorkingDirectory $InstallDir -NoNewWindow -Wait -PassThru
    }
    if ($proc -and $proc.ExitCode -ne 0) {
        Write-Warning "Setup wizard exited with code $($proc.ExitCode)."
    }
} else {
    Write-Host "Config already exists - skipping wizard." -ForegroundColor DarkGray
}
if (-not (Test-Path (Join-Path $InstallDir "agent.json"))) {
    Write-Host ""
    Write-Host "[ERROR] Setup wizard did not complete - agent.json was not created." -ForegroundColor Red
    Write-Host "Run the wizard manually to see the real error:" -ForegroundColor Yellow
    Write-Host ("    python " + [char]34 + (Join-Path $InstallDir "setup_wizard.py") + [char]34) -ForegroundColor Yellow
    Read-Host "Press Enter to exit"; exit 1
}

# ---------- 4) agent + firewall ----------
$agentInstall = Join-Path $InstallDir "install_agent.ps1"
if ($useExe) { & $agentInstall -InstallDir $InstallDir -AgentExe $AgentExe }
else         { & $agentInstall -InstallDir $InstallDir -PythonExe $PyExe }

# ---------- 5) shortcuts (all users, elevated) ----------
$cfg = Get-Content (Join-Path $InstallDir "agent.json") -Raw | ConvertFrom-Json
$tcpPort = if ($cfg.tcp_port) { [int]$cfg.tcp_port } else { 48720 }
$icon = Join-Path $InstallDir "classctl.ico"

function New-AdminShortcut([string]$lnkPath, [string]$target, [string]$arguments) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = $target
    $sc.Arguments  = $arguments
    $sc.WorkingDirectory = $InstallDir
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Description = "ClassCtl - Classroom Control"
    $sc.Save()
    # set the 'run as administrator' flag in the .lnk
    $b = [IO.File]::ReadAllBytes($lnkPath)
    $b[0x15] = $b[0x15] -bor 0x20
    [IO.File]::WriteAllBytes($lnkPath, $b)
}

if ($useExe) {
    $ctlTarget = Join-Path $InstallDir "classctl.exe"; $ctlArgs = ""
} else {
    $pyw = Join-Path (Split-Path $PyExe -Parent) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $PyExe }   # fall back to python.exe (console)
    $ctlTarget = $pyw
    $ctlArgs = "`"$(Join-Path $InstallDir 'controller.py')`" --config `"$controllerCfg`""
}
$publicDesktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$startMenu = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
New-AdminShortcut (Join-Path $publicDesktop "ClassCtl.lnk") $ctlTarget $ctlArgs
New-AdminShortcut (Join-Path $startMenu    "ClassCtl.lnk") $ctlTarget $ctlArgs
Write-Host "[OK] Shortcuts created (Desktop + Start Menu, all users)" -ForegroundColor Green

# ---------- 6) Add/Remove Programs ----------
$reg = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ClassCtl"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty $reg DisplayName    "ClassCtl - Classroom Control"
Set-ItemProperty $reg DisplayVersion "0.1.0"
Set-ItemProperty $reg Publisher      "ClassCtl"
Set-ItemProperty $reg InstallLocation $InstallDir
if (Test-Path $icon) { Set-ItemProperty $reg DisplayIcon $icon }
Set-ItemProperty $reg UninstallString "powershell -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
Set-ItemProperty $reg NoModify 1
Set-ItemProperty $reg NoRepair 1
Write-Host "[OK] Registered in Add/Remove Programs" -ForegroundColor Green

# ---------- 7) health check ----------
Write-Host "Checking that the agent is listening..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $tcpPort); $c.Close(); $ok = $true; break
    } catch { Start-Sleep -Milliseconds 500 }
}
if ($ok) { Write-Host "[OK] Agent is listening on TCP $tcpPort" -ForegroundColor Green }
else     { Write-Warning "Agent did not respond on port $tcpPort. Check the scheduled task and the firewall." }

# ---------- 8) computer-name pattern hint ----------
if ($env:COMPUTERNAME -notmatch '-') {
    Write-Host ("Note: computer name '$($env:COMPUTERNAME)' has no '-'. If you have not renamed it yet this is fine - rename after imaging (e.g. LAB1-12 / LAB1-INS). The controller re-checks at runtime.") -ForegroundColor DarkYellow
}

Write-Host "`n===== INSTALLATION COMPLETE =====" -ForegroundColor Green
Write-Host "Launch 'ClassCtl' from the Desktop. To uninstall: use Add/Remove Programs."
Read-Host "Press Enter to exit"
