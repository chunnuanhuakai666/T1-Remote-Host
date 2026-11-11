$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
})[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op)); $t.Wait(-1) | Out-Null; return $t.Result
}
function AwaitAction($op) {
    $t = $asTaskAction.Invoke($null, @($op)); $t.Wait(-1) | Out-Null
}

[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattCacheMode, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothCacheMode, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null

$selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
$devs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
$target = $devs | Where-Object { $_.Name -eq 'T1-Remote' } | Select-Object -First 1
if (-not $target) { Write-Output "T1-Remote not found"; exit 1 }

$ble = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($target.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
$addr = '{0:X12}' -f $ble.BluetoothAddress
Write-Output "DEVICE: $($ble.Name)  ADDR: $addr  STATUS: $($ble.ConnectionStatus)"

# device info
try {
    $di = Await ($ble.DeviceInformation) ([Windows.Devices.Enumeration.DeviceInformation])
} catch {}

$sres = Await ($ble.GetGattServicesAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
Write-Output "SERVICE STATUS: $($sres.Status)"
Write-Output ""

foreach ($s in $sres.Services) {
    $su = $s.Uuid.ToString()
    Write-Output ("--- SERVICE {0} ---" -f $su)
    $cres = Await ($s.GetCharacteristicsAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
    if ($cres.Status -ne 'Success') { Write-Output "    chars status: $($cres.Status)"; continue }
    foreach ($c in $cres.Characteristics) {
        $props = $c.CharacteristicProperties.ToString()
        # handle
        $h = '{0:X4}' -f $c.AttributeHandle
        $descTxt = ''
        try {
            $dres = Await ($c.GetDescriptorsAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDescriptorsResult])
            foreach ($dd in $dres.Descriptors) { $descTxt += (' desc=' + $dd.Uuid.ToString()) }
        } catch {}
        Write-Output ("    CHAR {0}  handle=0x{1}  props=[{2}]{3}" -f $c.Uuid.ToString(), $h, $props, $descTxt)
    }
}
$ble.Dispose()
