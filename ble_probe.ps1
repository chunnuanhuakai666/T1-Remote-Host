$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    return $t.Result
}

[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null

$selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
$devs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])

Write-Output "=== PAIRED BLE DEVICES: $($devs.Count) ==="
$rows = @()
foreach ($d in $devs) {
    $addr = ''
    if ($d.Id -match '([0-9A-Fa-f]{12})$') { $addr = $Matches[1] }
    $ble = $null
    try { $ble = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($d.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice]) } catch {}
    $conn = 'n/a'
    if ($ble) { $conn = $ble.ConnectionStatus.ToString() }
    $rows += [pscustomobject]@{ Name = $d.Name; Addr = $addr; Conn = $conn }
    if ($ble) { $ble.Dispose() }
}
$rows | Sort-Object Conn, Name | Format-Table -AutoSize
