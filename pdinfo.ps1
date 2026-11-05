param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$env:PYTHONPATH = "$PSScriptRoot\.deps;$PSScriptRoot" + $(if ($env:PYTHONPATH) { ";$env:PYTHONPATH" })
$pythonCommand = if (Test-Path "$PSScriptRoot\.venv\Scripts\python.exe") { "$PSScriptRoot\.venv\Scripts\python.exe" } elseif (Test-Path 'D:\KM003C\.venv\Scripts\python.exe') { 'D:\KM003C\.venv\Scripts\python.exe' } else { 'python' }
& $pythonCommand -m pdinfo @Arguments
exit $LASTEXITCODE
