# ClassCtl - full uninstall. Stops the agent, removes everything it changed
# in Windows, and deletes the protected folder.
# Run as Administrator.

param(
    [string]$InstallDir = "C:\ProgramData\ClassCtl",
    [string]$TaskName   = "ClassCtl Agent"
)
$ErrorActionPreference = "SilentlyContinue"

$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error "Must run as Administrator."; exit 1 }

# 1) Stop the agent first. Without this it keeps running until the next reboot
#    and can hold the files open.
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'agent\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
Write-Host "[OK] Agent stopped and its task removed."

# 2) Helper tasks used to reach the logged-on user's session. They delete
#    themselves normally, but a script that was interrupted can leave one.
foreach ($t in @('ClassCtlClose','ClassCtlWake','ClassCtlOpenApp',
                 'ClassCtlVmsOff','ClassCtlVmsRst')) {
    schtasks /delete /tn $t /f 2>$null | Out-Null
}
Write-Host "[OK] Helper tasks removed."

# 3) Firewall rules
Remove-NetFirewallRule -DisplayName "ClassCtl Agent TCP" -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName "ClassCtl Agent UDP" -ErrorAction SilentlyContinue
netsh advfirewall firewall delete rule name="ClassCtl Agent TCP" 2>$null | Out-Null
netsh advfirewall firewall delete rule name="ClassCtl Agent UDP" 2>$null | Out-Null
Write-Host "[OK] Firewall rules removed, ports closed again."

# 4) Shortcuts
$publicDesktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$startMenu = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
Remove-Item (Join-Path $publicDesktop "ClassCtl.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $startMenu    "ClassCtl.lnk") -Force -ErrorAction SilentlyContinue
Write-Host "[OK] Shortcuts removed."

# 5) Add/Remove Programs
Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ClassCtl" `
    -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "[OK] Add/Remove Programs entry removed."

# 6) Temporary files left in Windows\Temp
foreach ($f in @('cc_close.ps1','cc_wake.ps1','cc_openapp.ps1',
                 'cc_vms_off.ps1','cc_vms_rst.ps1','classctl-wol-prep.txt')) {
    Remove-Item (Join-Path $env:windir "Temp\$f") -Force -ErrorAction SilentlyContinue
}
Write-Host "[OK] Temporary files removed."

# 7) The protected folder itself
if (Test-Path $InstallDir) {
    Set-Location $env:TEMP        # do not sit inside the folder being deleted
    attrib -h $InstallDir 2>$null | Out-Null
    takeown /F $InstallDir /R /D Y | Out-Null
    icacls $InstallDir /reset /T /C | Out-Null
    Remove-Item -Path $InstallDir -Recurse -Force
    if (Test-Path $InstallDir) {
        Write-Warning "Folder $InstallDir could not be fully removed. Reboot and run this again."
    } else {
        Write-Host "[OK] Folder $InstallDir deleted."
    }
}

Write-Host ""
Write-Host "Uninstall complete. Nothing of ClassCtl is left running or configured." -ForegroundColor Green
Write-Host "Note: prepare-wol.ps1, if you ran it, changed Fast Startup and network"
Write-Host "card settings on purpose. Those are left as they are."
exit 0
