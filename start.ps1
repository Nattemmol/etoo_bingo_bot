# ============================================================
#  GoodBingo � One-click launcher
#  Starts cloudflared, auto-patches .env, then starts the bot.
# ============================================================

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   GoodBingo Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Locate cloudflared ----
$cfExe = Join-Path $Root "cloudflared.exe"
if (-not (Test-Path $cfExe)) {
    Write-Host "[ERROR] cloudflared.exe not found in project root." -ForegroundColor Red
    Write-Host "        Download it from: https://github.com/cloudflare/cloudflared/releases"
    exit 1
}

# ---- 1b. Free port 8765 if already in use ----
$portPids = (Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
if ($portPids) {
    Write-Host "   Freeing port 8765 (PIDs: $($portPids -join ','))..." -ForegroundColor DarkGray
    $portPids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
}

# Stop any leftover watchdog so it doesn't fight this fresh launch.
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'watchdog\.ps1' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Stop any stale cloudflared instances so they release files and port bindings
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# ---- 2. Start cloudflared and capture its URL ----
Write-Host "[1/3] Starting Cloudflare Tunnel..." -ForegroundColor Yellow

$tmpLog = Join-Path $env:TEMP "cf_tunnel_output_$PID.txt"
if (Test-Path $tmpLog) { Remove-Item $tmpLog -Force -ErrorAction SilentlyContinue }

$cfProcess = Start-Process `
    -FilePath $cfExe `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:8765" `
    -RedirectStandardError $tmpLog `
    -PassThru `
    -WindowStyle Minimized

Write-Host "   cloudflared PID: $($cfProcess.Id)" -ForegroundColor DarkGray
Write-Host "   Waiting for tunnel URL (up to 30s)..." -ForegroundColor DarkGray

$tunnelUrl = $null
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $tmpLog) {
        $content = Get-Content $tmpLog -Raw -ErrorAction SilentlyContinue
        if ($content -match "https://[a-z0-9\-]+\.trycloudflare\.com") {
            $tunnelUrl = $matches[0].Trim()
            break
        }
    }
}

if (-not $tunnelUrl) {
    Write-Host "[ERROR] Could not detect tunnel URL within 30 seconds." -ForegroundColor Red
    $cfProcess | Stop-Process -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "   Tunnel URL: $tunnelUrl" -ForegroundColor Green

# ---- 3. Update .env ----
Write-Host ""
Write-Host "[2/3] Updating .env..." -ForegroundColor Yellow

$envFile = Join-Path $Root ".env"
$envContent = Get-Content $envFile -Raw
if ($envContent -match "(?m)^WEBAPP_URL=.*$") {
    $envContent = $envContent -replace "(?m)^WEBAPP_URL=.*$", "WEBAPP_URL=$tunnelUrl"
} else {
    $envContent = $envContent.TrimEnd() + "`nWEBAPP_URL=$tunnelUrl`n"
}
Set-Content $envFile $envContent -NoNewline
Write-Host "   .env updated: WEBAPP_URL=$tunnelUrl" -ForegroundColor Green

# ---- 4. Start server + bot ----
Write-Host ""
Write-Host "[3/3] Starting server and bot..." -ForegroundColor Yellow

$python = Join-Path $Root ".venv\Scripts\python.exe"

$serverProcess = Start-Process `
    -FilePath $python `
    -ArgumentList "run.py" `
    -WorkingDirectory $Root `
    -PassThru `
    -WindowStyle Normal

Write-Host "   App PID: $($serverProcess.Id)" -ForegroundColor DarkGray

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   All services running!" -ForegroundColor Green
Write-Host ""
Write-Host "   Tunnel : $tunnelUrl" -ForegroundColor White
Write-Host "   Server : http://localhost:8765" -ForegroundColor White
Write-Host "   WebApp : $tunnelUrl" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press ENTER to stop everything and exit..." -ForegroundColor DarkGray
Read-Host | Out-Null

Write-Host "Stopping all processes..." -ForegroundColor Yellow
$cfProcess     | Stop-Process -Force -ErrorAction SilentlyContinue
$serverProcess | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Done. Goodbye!" -ForegroundColor Green
