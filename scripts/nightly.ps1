# Nightly checks. Run by the "AskNick nightly" scheduled task at 2 AM. Free checks only by default:
# tests, page audit, Docling third read, stress grade of the last run, chip check (needs the server).
# The paid stress run (every bank question through Claude, a few dollars) only runs when
# GUIDEBOT_NIGHTLY_STRESS=1 is set for the task. Nothing here writes the search index or approves anything.
# Morning report: data\nightly\<date>.md
# Register the task once (same headless conhost launch as "Guidebot server"):
#   Register-ScheduledTask -TaskName "AskNick nightly" -Trigger (New-ScheduledTaskTrigger -Daily -At 2:00AM) `
#     -Action (New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\nightly.ps1`"") `
#     -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3))
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$py = "$root\.venv\Scripts\python.exe"
$day = Get-Date -Format yyyy-MM-dd
$dir = Join-Path $root "data\nightly"
New-Item -ItemType Directory -Force $dir | Out-Null
$report = Join-Path $dir "$day.md"
$env:PYTHONIOENCODING = "utf-8"
$lines = @("# AskNick nightly $day", "")

function Step($name, [scriptblock]$run) {
    $t0 = Get-Date
    $out = & $run 2>&1 | Out-String
    $code = $LASTEXITCODE
    $secs = [int]((Get-Date) - $t0).TotalSeconds
    $tail = ($out.Trim() -split "`r?`n" | Select-Object -Last 4) -join "`n"
    $mark = if ($code -eq 0) { "ok" } else { "FAILED (exit $code)" }
    $script:lines += @("## $name : $mark, ${secs}s", "", '```', $tail, '```', "")
}

Step "Tests" { $env:GUIDEBOT_RERANK = "0"; & $py -m pytest -q -p no:cacheprovider; $env:GUIDEBOT_RERANK = $null }
Step "Page audit (data/audit/pages.md)" { & $py -m tools.page_audit }
Step "Docling third read (data/audit/docling.md)" { & $py -m tools.docling_check }

$serverUp = $true
try { Invoke-RestMethod http://127.0.0.1:8600/api/programs -TimeoutSec 10 | Out-Null } catch { $serverUp = $false }
if ($env:GUIDEBOT_NIGHTLY_STRESS -eq "1" -and $serverUp) {
    Step "Stress run (paid)" { & $py -m tools.stress_run }
}
Step "Stress check (data/stress/report.md)" { & $py -m tools.stress_check }
if ($serverUp) {
    Step "Chip check (data/stress/chips-answers.md)" { & $py -m tools.chip_check }
} else {
    $lines += @("## Chip check : skipped, server not running", "")
}

$lines | Set-Content -Encoding utf8 $report
