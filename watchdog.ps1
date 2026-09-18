$mutex = New-Object System.Threading.Mutex($false, "GoodBingoWatchdog")
if (-not $mutex.WaitOne(0)) {
    exit
}

$python = "C:\Users\kalki\Desktop\3rd year\DSA\Etoo_bot\.venv\Scripts\python.exe"
$runpy = "C:\Users\kalki\Desktop\3rd year\DSA\Etoo_bot\run.py"

while ($true) {
    try {
        $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        $runpyProcs = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' AND CommandLine LIKE '%run.py%'")

        if (-not $listener) {
            if ($runpyProcs.Count -gt 0) {
                foreach ($proc in $runpyProcs) {
                    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                }
                Start-Sleep -Seconds 2
            }
            & "$env:ComSpec" /c "start `"runpy`" /min `"$python`" `"$runpy`"" | Out-Null
            Start-Sleep -Seconds 15
        }
        elseif ($runpyProcs.Count -gt 1) {
            foreach ($proc in $runpyProcs) {
                if ($proc.ProcessId -ne [int]$listener) {
                    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    catch {
        $Error.Clear()
    }
    [System.GC]::KeepAlive($mutex)
    Start-Sleep -Seconds 5
}