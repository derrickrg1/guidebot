# Agency change tracker, standalone run: Fannie Mae announcements and Lender Letters, plus FHA/VA releases the
# server's agency check already downloaded, summarized into data/agency_changes.jsonl. Safe while the server runs
# (reads agency/, never writes it or the search index). The server's weekly agency check does the same thing on
# its own, so this is only a backup for weeks the server was off.
#
# Register it (Derrick runs this once, in PowerShell):
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$env:USERPROFILE\OneDrive\Desktop\Revo Funding\01 Tools & Apps\guidebot\scripts\run_tracker.ps1`""
#   Register-ScheduledTask -TaskName "AskNick tracker" -Action $a -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At 6am)
# Log: %LOCALAPPDATA%\guidebot\tracker.log
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $env:LOCALAPPDATA "guidebot"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "tracker.log"
"=== $(Get-Date -Format s) ===" | Out-File -Append -Encoding utf8 $log
& "$root\.venv\Scripts\python.exe" -m guidebot.tracker 2>&1 | Out-File -Append -Encoding utf8 $log
