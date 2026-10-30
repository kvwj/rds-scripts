$port = [System.IO.Ports.SerialPort]::new(
    "COM3", 9600,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
)

try {
    $port.Open()
    Start-Sleep -Milliseconds 300

    $open = [byte[]](0x55,0x56,0x00,0x00,0x00,0x01,0x01,0xAD)
    $close = [byte[]](0x55,0x56,0x00,0x00,0x00,0x01,0x02,0xAE)

    $port.Write($open, 0, $open.Length)
    Start-Sleep -Seconds 2
    $port.Write($close, 0, $close.Length)
}
finally {
    if ($port.IsOpen) { $port.Close() }
}
