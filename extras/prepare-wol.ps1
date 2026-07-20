# ClassCtl - prepare a station for Wake-on-LAN.
# Machine level, so the agent can run it as SYSTEM. Run once per image,
# before cloning, or push it to a room from the console.
#
# BIOS/UEFI must also have Wake on LAN enabled - firmware cannot be set from here.

$report = @()

# 1) Fast Startup. This is the usual reason WoL "does not work": with it on,
#    a normal shutdown leaves the machine in a hybrid state that ignores the
#    magic packet.
try {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
    Set-ItemProperty -Path $key -Name HiberbootEnabled -Value 0 -Type DWord -Force
    $report += "Fast Startup: disabled"
} catch {
    $report += "Fast Startup: FAILED - $($_.Exception.Message)"
}

# 2) Wired adapters: allow the magic packet to wake the machine, and stop
#    Windows powering the card down.
$wired = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
         Where-Object { $_.MediaType -ne 'Native 802.11' -and $_.InterfaceDescription -notmatch 'Wi-?Fi|Wireless' }

if (-not $wired) {
    $report += "No wired adapter found - Wake-on-LAN over Wi-Fi is not reliable"
}

foreach ($nic in $wired) {
    $name = $nic.Name
    try {
        Set-NetAdapterPowerManagement -Name $name `
            -WakeOnMagicPacket Enabled `
            -WakeOnPattern Disabled `
            -AllowComputerToTurnOffDevice Disabled `
            -DeviceSleepOnDisconnect Disabled `
            -ErrorAction Stop
        $report += "$name : magic packet enabled"
    } catch {
        $report += "$name : power management not settable - $($_.Exception.Message)"
    }

    # Some drivers only expose these as advanced properties.
    foreach ($pair in @(@('*WakeOnMagicPacket',1), @('*WakeOnPattern',0), @('*PMARPOffload',0), @('*PMNSOffload',0))) {
        try {
            Set-NetAdapterAdvancedProperty -Name $name -RegistryKeyword $pair[0] `
                -RegistryValue $pair[1] -NoRestart -ErrorAction Stop
        } catch { }
    }
}

# 3) Show what the machine ended up with, so a push to a room can be checked.
$state = @()
foreach ($nic in $wired) {
    try {
        $pm = Get-NetAdapterPowerManagement -Name $nic.Name -ErrorAction Stop
        $mac = $nic.MacAddress
        $state += "$($nic.Name) mac=$mac magic=$($pm.WakeOnMagicPacket)"
    } catch { }
}

$out = "$env:windir\Temp\classctl-wol-prep.txt"
($report + $state) | Set-Content -LiteralPath $out -Encoding UTF8

# Fail loudly if the important part did not take, so the console reports it.
$ok = $true
foreach ($nic in $wired) {
    try {
        $pm = Get-NetAdapterPowerManagement -Name $nic.Name -ErrorAction Stop
        if ($pm.WakeOnMagicPacket -ne 'Enabled') { $ok = $false }
    } catch { $ok = $false }
}
if (-not $wired) { $ok = $false }

if (-not $ok) {
    Write-Error "Wake-on-LAN could not be enabled on every wired adapter. See $out"
    exit 1
}
exit 0
