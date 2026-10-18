# Starts YOUR normal Chrome (or Edge) with a local DevTools port so the agent can
# read the booking data while YOU look up your own booking by hand.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_chrome_for_capture.ps1
#
# - Uses a separate profile folder (Chrome requires this for remote debugging),
#   so your everyday profile, passwords and cookies are not exposed.
# - The port listens on 127.0.0.1 only. Close this Chrome window when done.

param([int]$Port = 9222)

$profileDir = Join-Path $env:LOCALAPPDATA "AskMyBooking\chrome-capture-profile"
$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { Write-Error "Chrome/Edge not found"; exit 1 }

New-Item -ItemType Directory -Force $profileDir | Out-Null
Start-Process $browser -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=`"$profileDir`"",
    "--no-first-run",
    "https://www.goindigo.in/edit-booking.html"
)
Write-Host "Started $browser on 127.0.0.1:$Port"
Write-Host "Now run the agent with source 'live_indigo' and look up your booking in that window."
