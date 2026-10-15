# Starts winremote outside Cursor Job Object via Python CREATE_BREAKAWAY_FROM_JOB.
$ErrorActionPreference = "Stop"
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$starter = Join-Path $env:USERPROFILE ".config\winremote\start-winremote-detached.py"
& $py $starter
exit $LASTEXITCODE
