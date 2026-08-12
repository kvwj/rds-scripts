# --- Configuration ---
$portName = "COM3"
$baudRate = 9600
$parity   = [System.IO.Ports.Parity]::None
$dataBits = 8
$stopBits = [System.IO.Ports.StopBits]::One

# --- Initialize Serial Port ---
$port = New-Object System.IO.Ports.SerialPort($portName, $baudRate, $parity, $dataBits, $stopBits)

try {
    Write-Host "Opening connection to $portName..."
    $port.Open()
    Start-Sleep -Seconds 2 # Allow connection to settle

    # --- Send Commands ---
    # Note: Replace [byte[]](0x01) with your specific hex protocol commands
    Write-Host "Sending Turn ON command..."
    $cmdOn = [byte[]](0x01) 
    $port.Write($cmdOn, 0, $cmdOn.Length)
    
    Start-Sleep -Seconds 2 # Keep it on for 2 seconds

    Write-Host "Sending Turn OFF command..."
    $cmdOff = [byte[]](0x02)
    $port.Write($cmdOff, 0, $cmdOff.Length)

}
catch {
    Write-Error "Failed to communicate with TB351: $_"
}
finally {
    # Always ensure the port closes cleanly
    if ($port -and $port.IsOpen) {
        $port.Close()
        Write-Host "Serial port closed safely."
    }
}
