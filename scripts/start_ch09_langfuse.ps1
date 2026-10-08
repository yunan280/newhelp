param([switch]$WaitForHealthy)
$ErrorActionPreference='Stop'
$ch09Root=Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ch09Root
try {
    & ./.venv-ch09/Scripts/python.exe -X utf8 scripts/init_ch09_langfuse.py
    if($LASTEXITCODE -ne 0) { throw 'Langfuse credential initialization failed' }
    $ch09Args=@('compose','--env-file','.env.ch09.langfuse','-f','infra/langfuse/compose.yml','up','-d')
    if($WaitForHealthy) { $ch09Args += @('--wait','--wait-timeout','300') }
    & docker @ch09Args
    if($LASTEXITCODE -ne 0) { throw 'Langfuse deployment failed' }
    Write-Output 'Langfuse: http://127.0.0.1:3039 (local credentials in ignored .env.ch09.langfuse)'
} finally { Pop-Location }
