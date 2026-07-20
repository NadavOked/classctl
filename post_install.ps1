# ClassCtl - shortcuts and Add/Remove registration. Called by the wizard, hidden.
param(
    [string]$InstallDir = "C:\ProgramData\ClassCtl",
    [string]$PythonExe  = ""
)
$ErrorActionPreference = "Stop"

$controllerCfg = Join-Path $InstallDir "controller.json"
$icon = Join-Path $InstallDir "classctl.ico"

if ($PythonExe -and (Test-Path $PythonExe)) {
    $pyw = Join-Path (Split-Path $PythonExe -Parent) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $PythonExe }
} else {
    $pyw = "pythonw.exe"
}
$target = $pyw
$argline = "`"$(Join-Path $InstallDir 'controller.py')`" --config `"$controllerCfg`""

function New-AdminShortcut([string]$lnkPath) {
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = $target
    $sc.Arguments  = $argline
    $sc.WorkingDirectory = $InstallDir
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Description = "ClassCtl - classroom operations console"
    $sc.Save()
    $b = [IO.File]::ReadAllBytes($lnkPath)
    $b[0x15] = $b[0x15] -bor 0x20      # run as administrator
    [IO.File]::WriteAllBytes($lnkPath, $b)
}

$publicDesktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
$startMenu = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"
New-AdminShortcut (Join-Path $publicDesktop "ClassCtl.lnk")
New-AdminShortcut (Join-Path $startMenu    "ClassCtl.lnk")

$reg = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ClassCtl"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty $reg DisplayName     "ClassCtl - Classroom Control"
Set-ItemProperty $reg DisplayVersion  "0.1.0"
Set-ItemProperty $reg Publisher       "ClassCtl"
Set-ItemProperty $reg InstallLocation $InstallDir
if (Test-Path $icon) { Set-ItemProperty $reg DisplayIcon $icon }
Set-ItemProperty $reg UninstallString "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstallDir\uninstall.ps1`""
Set-ItemProperty $reg NoModify 1
Set-ItemProperty $reg NoRepair 1
