# One-click demo start: checks the keys, starts the voice server and the tunnel in their own
# windows, opens the dashboard, and puts today's Retell address on the clipboard.
# Double-click start_demo.bat to run this.

$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj
$Host.UI.RawUI.WindowTitle = "ResolvOps Front Desk: setup"

function Say($text) { Write-Host $text }
function Good($text) { Write-Host $text -ForegroundColor Green }
function Bad($text) { Write-Host $text -ForegroundColor Red }

Say ""
Say "ResolvOps Front Desk: demo setup"
Say "================================"

# 1. Keys and internet
Say ""
Say "1/4  Checking Nemotron and Tavily keys (needs internet)..."
& python check_keys.py 2>&1 | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) {
    Bad "Key check failed. Fix the internet connection (try the phone hotspot) and run this again."
    Read-Host "Press Enter to close"
    exit 1
}

# 2. Voice server (own window) unless one is already up
Say ""
Say "2/4  Starting the voice server and dashboard..."
function ServerUp {
    # curl.exe ships with Windows and ignores proxy settings that make Invoke-WebRequest time out on localhost
    $out = & curl.exe -s -m 2 http://localhost:8000/health 2>$null
    return ("$out" -match '"ok"')
}
$alreadyUp = ServerUp
if ($alreadyUp) {
    Good "     A server is already running on port 8000, keeping it."
} else {
    Start-Process -FilePath "$proj\run_server.bat" -WorkingDirectory $proj
    $tries = 0
    do { Start-Sleep -Seconds 1; $tries++; $alreadyUp = ServerUp } while (-not $alreadyUp -and $tries -lt 60)
    if ($alreadyUp) { Good "     Server is up." } else { Bad "     Server did not start. Look at the 'Voice server' window for the error."; Read-Host "Press Enter to close"; exit 1 }
}

# 3. Tunnel (own window), logging to tunnel.log so we can read the address
Say ""
Say "3/4  Opening the public tunnel..."
$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
$cfPath = if ($cf) { $cf.Source } elseif (Test-Path "C:\Program Files (x86)\cloudflared\cloudflared.exe") { "C:\Program Files (x86)\cloudflared\cloudflared.exe" } elseif (Test-Path "C:\Program Files\cloudflared\cloudflared.exe") { "C:\Program Files\cloudflared\cloudflared.exe" } else { $null }
if (-not $cfPath) {
    Bad "     cloudflared not found. Install with: winget install Cloudflare.cloudflared"
    Bad "     Or open the tunnel by hand:  ssh -R 80:localhost:8000 nokey@localhost.run"
    Read-Host "Press Enter to close"
    exit 1
}
function TunnelUrlFromLog {
    if (-not (Test-Path "$proj\tunnel.log")) { return $null }
    $m = Select-String -Path "$proj\tunnel.log" -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Value }
    return $null
}
function TunnelAnswers($u) {
    $out = & curl.exe -s -m 8 "$u/health" 2>$null
    return ("$out" -match '"ok"')
}

# Reuse a tunnel that is already running and answering; otherwise start a fresh one.
$url = $null
$running = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($running) {
    $existing = TunnelUrlFromLog
    if ($existing -and (TunnelAnswers $existing)) {
        $url = $existing
        Good "     A tunnel is already running, keeping it."
    } else {
        Say "     A stale tunnel is running; restarting it."
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}
function StartTunnel($protocol) {
    Remove-Item "$proj\tunnel.log" -Force -ErrorAction SilentlyContinue
    $a = @("tunnel","--url","http://localhost:8000","--no-autoupdate","--logfile","$proj\tunnel.log")
    if ($protocol) { $a += @("--protocol", $protocol) }
    Start-Process -FilePath $cfPath -ArgumentList $a -WorkingDirectory $proj -WindowStyle Minimized
}
if (-not $url) {
    # Try the fast default protocol (QUIC/UDP) first
    StartTunnel $null
    $tries = 0
    do { Start-Sleep -Seconds 1; $tries++; $url = TunnelUrlFromLog } while (-not $url -and $tries -lt 20)
    if (-not $url) {
        # Guest/venue wifi commonly blocks QUIC's UDP; fall back to http2 over TCP
        Say "     Fast protocol blocked (common on guest wifi); switching to compatibility mode..."
        Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        StartTunnel "http2"
        $tries = 0
        do { Start-Sleep -Seconds 1; $tries++; $url = TunnelUrlFromLog } while (-not $url -and $tries -lt 35)
    }
}

if (-not $url) {
    Bad "     No tunnel address after 45 seconds. Check the 'Tunnel' window; the venue wifi may block it. Try the phone hotspot."
    Read-Host "Press Enter to close"
    exit 1
}

$wss = $url.Replace("https://", "wss://") + "/llm-websocket"
try { Set-Clipboard -Value $wss } catch {}

# 4. Dashboard, and point the Retell agent at today's address when the API key is in .env
Say ""
Say "4/4  Opening the dashboard and updating Retell..."
Start-Process "http://localhost:8000"
& python retell_sync.py 2>&1 | ForEach-Object { Say "     $_" }

Say ""
Good "READY."
Say ""
Say "Today's Retell address (already on your clipboard, just paste it into the agent's Custom LLM URL):"
Say ""
Write-Host "    $wss" -ForegroundColor Yellow
Say ""
Say "Webhook (optional, Retell agent settings):  $url/webhook"
Say "Prove the tunnel:  python tests/tunnel_smoke.py $url"
Say ""
Say "Leave the 'Voice server' and 'Tunnel' windows open. Close this one whenever."
Read-Host "Press Enter to close this window"
