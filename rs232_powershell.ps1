$portName = "COM3"
$baudRate = 9600
$parity   = [System.IO.Ports.Parity]::None
$dataBits = 8
$stopBits = [System.IO.Ports.StopBits]::One

$port = New-Object System.IO.Ports.SerialPort(
    $portName, $baudRate, $parity, $dataBits, $stopBits
)

try {
    Write-Host "Opening connection to $portName..."
    $port.Open()
    Start-Sleep -Milliseconds 250

    Write-Host "Opening relay channel 1..."
    $cmdOpen = [byte[]](0x55, 0x56, 0x00, 0x00, 0x00, 0x01, 0x01, 0xAD)
    $port.Write($cmdOpen, 0, $cmdOpen.Length)
}
catch {
    Write-Error "Failed to communicate with relay card: $_"
}
finally {
    if ($port -and $port.IsOpen) {
        $port.Close()
        Write-Host "Serial port closed safely."
    }
}
