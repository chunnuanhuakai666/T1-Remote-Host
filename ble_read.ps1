$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op)); $t.Wait(-1) | Out-Null; return $t.Result
}

[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothCacheMode, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

$selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
$devs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
$target = $devs | Where-Object { $_.Name -eq 'T1-Remote' } | Select-Object -First 1
$ble = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($target.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
Write-Output ("DEVICE {0}  ADDR {1:X12}  STATUS {2}" -f $ble.Name, $ble.BluetoothAddress, $ble.ConnectionStatus)

$uc = [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached

function ReadChar($svcUuid, $charUuid) {
    $gs = Await ($ble.GetGattServicesForUuidAsync([guid]$svcUuid, $uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
    if ($gs.Status -ne 'Success' -or $gs.Services.Count -eq 0) { return "[service $svcUuid unavailable: $($gs.Status)]" }
    $s = $gs.Services[0]
    $cr = Await ($s.GetCharacteristicsForUuidAsync([guid]$charUuid, $uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
    if ($cr.Status -ne 'Success' -or $cr.Characteristics.Count -eq 0) { return "[char $charUuid unavailable: $($cr.Status)]" }
    $c = $cr.Characteristics[0]
    $rr = Await ($c.ReadValueAsync($uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult])
    if ($rr.Status -ne 'Success') { return "[read error $($rr.Status)]" }
    $buf = $rr.Value
    $bytes = $null
    try {
        $bytes = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::ToArray($buf)
    } catch {
        try {
            Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue | Out-Null
            $bytes = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::ToArray($buf)
        } catch { return "[buffer decode failed: $($_.Exception.Message)]" }
    }
    if ($null -eq $bytes) { return "[null buffer]" }
    $len = $bytes.Length
    if ($len -eq 0) { return "[empty]" }
    $hex = ($bytes | ForEach-Object { $_.ToString('X2') }) -join ' '
    $ascii = -join ($bytes | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
    return "hex[$len]= $hex   ascii= $ascii"
}

Write-Output ""
Write-Output "--- Device Information Service (0x180A) ---"
Write-Output ("2A29 Manufacturer  : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a29-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A24 Model Number  : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a24-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A25 Serial Number : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a25-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A27 HW Revision   : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a27-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A26 SW Revision   : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a26-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A28 SW Revision2  : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a28-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A23 System ID     : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a23-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A2A IEEE Cert     : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a2a-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A50 PnP ID        : {0}" -f (ReadChar '0000180a-0000-1000-8000-00805f9b34fb' '00002a50-0000-1000-8000-00805f9b34fb'))

Write-Output ""
Write-Output "--- GAP / connection params ---"
Write-Output ("2A00 Device Name   : {0}" -f (ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a00-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A01 Appearance    : {0}" -f (ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a01-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A04 Conn Params   : {0}" -f (ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a04-0000-1000-8000-00805f9b34fb'))

Write-Output ""
Write-Output "--- Battery ---"
Write-Output ("2A19 Battery Level : {0}" -f (ReadChar '0000180f-0000-1000-8000-00805f9b34fb' '00002a19-0000-1000-8000-00805f9b34fb'))

Write-Output ""
Write-Output "--- Vendor channel AB5E0001 ---"
foreach ($cu in @('ab5e0002','ab5e0003','ab5e0004')) {
    Write-Output ("{0} : {1}" -f $cu, (ReadChar 'ab5e0001-5a21-4f05-bc7d-af01f617b664' "$cu-5a21-4f05-bc7d-af01f617b664"))
}

Write-Output ""
Write-Output "--- HID service 0x1812 report map / reports ---"
Write-Output ("2A4B Report Map    : {0}" -f (ReadChar '00001812-0000-1000-8000-00805f9b34fb' '00002a4b-0000-1000-8000-00805f9b34fb'))
Write-Output ("2A4D Report (in)   : {0}" -f (ReadChar '00001812-0000-1000-8000-00805f9b34fb' '00002a4d-0000-1000-8000-00805f9b34fb'))

$ble.Dispose()
