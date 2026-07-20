# ClassCtl - install the agent as a Scheduled Task running as SYSTEM at boot.
# Run as Administrator.
#
#   .\install_agent.ps1                      # uses Python from PATH
#   .\install_agent.ps1 -AgentExe agent.exe  # use an exe instead of Python

param(
    [string]$InstallDir = "C:\ProgramData\ClassCtl",
    [string]$AgentExe   = "",           # empty -> use python
    [string]$PythonExe  = "",           # full path to python.exe (from install.ps1)
    [string]$TaskName   = "ClassCtl Agent"
)

$ErrorActionPreference = "Stop"

# require administrator
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error "Must run as Administrator."; exit 1 }

$agentConfig = Join-Path $InstallDir "agent.json"
if (-not (Test-Path $agentConfig)) {
    Write-Error "Missing $agentConfig - run setup_wizard.py first."; exit 1
}

# read the ports from the config so we open exactly what is needed
try {
    $cfg = Get-Content $agentConfig -Raw | ConvertFrom-Json
    $tcpPort = if ($cfg.tcp_port) { [int]$cfg.tcp_port } else { 48720 }
    $udpPort = if ($cfg.udp_port) { [int]$cfg.udp_port } else { 48719 }
} catch {
    $tcpPort = 48720; $udpPort = 48719
}

if ($AgentExe -ne "") {
    $exe  = (Resolve-Path $AgentExe).Path
    $agentArgs = "--config `"$agentConfig`""
} else {
    $py = $PythonExe
    if (-not $py -or -not (Test-Path $py)) {
        foreach ($c in (Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and (Test-Path $c.Source) -and ($c.Source -notlike "*\WindowsApps\*")) { $py=$c.Source; break }
        }
    }
    if (-not $py -or -not (Test-Path $py)) { Write-Error "Real python.exe not found. Pass -PythonExe or use -AgentExe."; exit 1 }
    $exe  = $py
    $agentArgs = "`"$(Join-Path $InstallDir 'agent.py')`" --config `"$agentConfig`""
}

$action    = New-ScheduledTaskAction -Execute $exe -Argument $agentArgs -WorkingDirectory $InstallDir
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# ---------- firewall: check, open, verify ----------
function Ensure-FwRule {
    param([string]$Name, [string]$Proto, [int]$Port)
    # drop any old rule and recreate it, so this is idempotent
    Remove-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    try {
        New-NetFirewallRule -DisplayName $Name -Direction Inbound -Action Allow `
            -Protocol $Proto -LocalPort $Port -Profile Any -Enabled True `
            -ErrorAction Stop | Out-Null
    } catch {
        # fall back to netsh when the NetSecurity module is unavailable
        netsh advfirewall firewall delete rule name="$Name" | Out-Null
        netsh advfirewall firewall add rule name="$Name" dir=in action=allow `
            protocol=$Proto localport=$Port | Out-Null
    }
}

Write-Host "`nChecking and updating firewall..." -ForegroundColor Cyan

# report profile state
try {
    foreach ($p in (Get-NetFirewallProfile -ErrorAction Stop)) {
        $st = if ($p.Enabled) { "enabled" } else { "disabled" }
        Write-Host "  Profile $($p.Name): firewall $st"
    }
} catch { Write-Host "  (could not read firewall profiles)" }

Ensure-FwRule -Name "ClassCtl Agent TCP" -Proto TCP -Port $tcpPort
Ensure-FwRule -Name "ClassCtl Agent UDP" -Proto UDP -Port $udpPort

# verify the rules exist and are enabled
$rt = Get-NetFirewallRule -DisplayName "ClassCtl Agent TCP" -ErrorAction SilentlyContinue
$ru = Get-NetFirewallRule -DisplayName "ClassCtl Agent UDP" -ErrorAction SilentlyContinue
$tcpOk = $rt -and ($rt.Enabled -eq $true -or $rt.Enabled -eq "True")
$udpOk = $ru -and ($ru.Enabled -eq $true -or $ru.Enabled -eq "True")
if ($tcpOk -and $udpOk) {
    Write-Host "[OK] Firewall: TCP $tcpPort and UDP $udpPort open and verified." -ForegroundColor Green
} else {
    Write-Warning ("Could not verify all firewall rules. " +
                   "Check manually that inbound TCP $tcpPort and UDP $udpPort are allowed.")
}
Write-Host "  Note: allow these ports in any third-party firewall/antivirus too." -ForegroundColor DarkYellow

Start-ScheduledTask -TaskName $TaskName
Write-Host "`n[OK] Agent installed and running as SYSTEM. Task: '$TaskName'" -ForegroundColor Green
Write-Host "  Command: $exe $agentArgs"
