$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
& "$ProjectRoot\.venv-ch03\Scripts\python.exe" -m mewhelp.knowledge.cli mine
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$ProjectRoot\.venv-ch03\Scripts\python.exe" -m mewhelp.knowledge.cli sync
exit $LASTEXITCODE
