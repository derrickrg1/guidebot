# Keeps the AskNick server running. Run by the "Guidebot server" scheduled task at login.
# Task Scheduler's "restart on failure" only covers a task that fails to start, not a server that crashes
# later, so this loop relaunches it. The log is appended to, so the crash that caused a restart stays in it.
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $env:LOCALAPPDATA "guidebot"
$log = Join-Path $logDir "server.log"
New-Item -ItemType Directory -Force $logDir | Out-Null
Set-Location -LiteralPath $root

while ($true) {
    # Keep the log from growing forever: start fresh once it passes 5 MB.
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 5MB) { Move-Item -Force $log "$log.old" }
    "==== $(Get-Date -Format s) starting server" >> $log
    $started = Get-Date
    & "$root\.venv\Scripts\python.exe" -u -m guidebot.server *>> $log
    "==== $(Get-Date -Format s) server exited with code $LASTEXITCODE, restarting" >> $log
    # Markers use >> like the server output above, so the whole log has one encoding.
    # A quick exit (port taken, bad edit) would spin; wait longer then.
    Start-Sleep -Seconds $(if (((Get-Date) - $started).TotalSeconds -lt 30) { 30 } else { 5 })
}
