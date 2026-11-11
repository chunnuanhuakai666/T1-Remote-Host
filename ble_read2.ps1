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
[Windows.Storage.Streams.IBuffer, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

$selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
$devs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
$target = $devs | Where-Object { $_.Name -eq 'T1-Remote' } | Select-Object -First 1
$ble = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($target.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
$uc = [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached

function ReadChar($svcUuid, $charUuid, $label) {
    try {
        $gs = Await ($ble.GetGattServicesForUuidAsync([guid]$svcUuid, $uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
        if ($gs.Status -ne 'Success' -or $gs.Services.Count -eq 0) { Write-Output ("{0,-18}: [svc {1}]" -f $label, $gs.Status); return }
        $s = $gs.Services[0]
        $cr = Await ($s.GetCharacteristicsForUuidAsync([guid]$charUuid, $uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
        if ($cr.Status -ne 'Success' -or $cr.Characteristics.Count -eq 0) { Write-Output ("{0,-18}: [char {1}]" -f $label, $cr.Status); return }
        $c = $cr.Characteristics[0]
        $rr = Await ($c.ReadValueAsync($uc)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult])
        if ($rr.Status -ne 'Success') { Write-Output ("{0,-18}: [read {1}]" -f $label, $rr.Status); return }
        $ib = $rr.Value -as [Windows.Storage.Streams.IBuffer]
        if ($null -eq $ib) { Write-Output ("{0,-18}: [buffer cast failed]" -f $label); return }
        $len = [int]$ib.Length
        if ($len -eq 0) { Write-Output ("{0,-18}: <empty>" -f $label); return }
        $dr = [Windows.Storage.Streams.DataReader]::FromBuffer($ib)
        $bytes = New-Object byte[] $len
        $dr.ReadBytes($bytes)
        $hex = ($bytes | ForEach-Object { $_.ToString('X2') }) -join ' '
        $asc = -join ($bytes | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
        Write-Output ("{0,-18}: {1,-50} | {2}" -f $label, $hex, $asc)
    } catch {
        Write-Output ("{0,-18}: EXC {1}" -f $label, $_.Exception.Message)
    }
}

$DIS = '0000180a-0000-1000-8000-00805f9b34fb'
Write-Output "--- Device Information (180A) ---"
ReadChar $DIS '00002a29-0000-1000-8000-00805f9b34fb' 'Manufacturer'
ReadChar $DIS '00002a24-0000-1000-8000-00805f9b34fb' 'Model'
ReadChar $DIS '00002a25-0000-1000-8000-00805f9b34fb' 'SerialNumber'
ReadChar $DIS '00002a27-0000-1000-8000-00805f9b34fb' 'HwRevision'
ReadChar $DIS '00002a26-0000-1000-8000-00805f9b34fb' 'FwRevision'
ReadChar $DIS '00002a28-0000-1000-8000-00805f9b34fb' 'SwRevision'
ReadChar $DIS '00002a23-0000-1000-8000-00805f9b34fb' 'SystemId'
ReadChar $DIS '00002a50-0000-1000-8000-00805f9b34fb' 'PnpId'

Write-Output ""
Write-Output "--- GAP / Battery ---"
ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a00-0000-1000-8000-00805f9b34fb' 'DeviceName'
ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a01-0000-1000-8000-00805f9b34fb' 'Appearance'
ReadChar '00001800-0000-1000-8000-00805f9b34fb' '00002a04-0000-1000-8000-00805f9b34fb' 'ConnParams'
ReadChar '0000180f-0000-1000-8000-00805f9b34fb' '00002a19-0000-1000-8000-00805f9b34fb' 'BatteryLevel'

Write-Output ""
Write-Output "--- Vendor AB5E0001 ---"
ReadChar 'ab5e0001-5a21-4f05-bc7d-af01f617b664' 'ab5e0002-5a21-4f05-bc7d-af01f617b664' 'AB5E0002'
ReadChar 'ab5e0001-5a21-4f05-bc7d-af01f617b664' 'ab5e0003-5a21-4f05-bc7d-af01f617b664' 'AB5E0003'
ReadChar 'ab5e0001-5a21-4f05-bc7d-af01f617b664' 'ab5e0004-5a21-4f05-bc7d-af01f617b664' 'AB5E0004'

$ble.Dispose()
