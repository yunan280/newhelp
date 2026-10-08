param([ValidateRange(1,65535)][int]$Port=9030,
      [int]$ModelContextWindow=32768,
      [string]$RouterCalibration='artifacts/ch09/20261008-native/router/router.json',
      [string]$ConfidenceProfile='config/ch09-confidence.json',
      [string]$ContextCalibration='artifacts/ch07/20261004-native/tokens-calibration-03/context-profile.json',
      [string]$PolicyCalibration='artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json',
      [string]$Collection='ch06_eval_mysql_20261002_01',
      [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$repoPath=Split-Path -Parent $PSScriptRoot
$pythonPath=Join-Path $repoPath '.venv-ch09/Scripts/python.exe'
$serveScript=Join-Path $repoPath 'scripts/preflight_ch09.py'
$runtimeValues=@{
    CH09_ENABLED='true'; CH09_ARTIFACT_DIR=Join-Path $repoPath 'artifacts/ch09/20261008-native'
    CH09_CONFIDENCE_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $ConfidenceProfile)).Path
    CH06_PRIMARY_MODEL='deepseek-v4-pro'; CH06_CASCADE_ENABLED='false'
    CH06_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $RouterCalibration)).Path
    CH06_ROUTER_DATASET_PATH=Join-Path $repoPath 'eval/ch08/router'
    CONTEXT_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $ContextCalibration)).Path
    CH06_POLICY_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $PolicyCalibration)).Path
    CH08_TOOL_CONFIG=Join-Path $repoPath 'config/ch08-tools.json'
    CH08_PLUGIN_DIR=Join-Path $repoPath 'tool_plugins'
    MILVUS_COLLECTION=$Collection
    RAG_CALIBRATION_PATH=Join-Path $repoPath 'artifacts/ch04/ch04_20260930_03/calibration.json'
    CH05_CHECKPOINT_PATH=Join-Path $repoPath '.cache/ch09/checkpoints.sqlite3'
    MODEL_CONTEXT_WINDOW=[string]$ModelContextWindow; MAX_OUTPUT_TOKENS='2000'; MAX_USER_INPUT_TOKENS='2000'
    MAX_AGENT_STEPS='4'; TOOL_RESULT_MAX_TOKENS='1200'; RERANK_TOP_K='5'
    PYTHONUTF8='1'; PYTHONIOENCODING='utf-8'
}
$previousValues=@{}
Push-Location -LiteralPath $repoPath
try {
    $privateJson=& $pythonPath -X utf8 -c "import json; from dotenv import dotenv_values; d=dotenv_values('.env.ch09.langfuse'); print(json.dumps({k:d[k] for k in ('LANGFUSE_BASE_URL','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY')}))"
    if($LASTEXITCODE -ne 0) { throw 'Local Langfuse environment is unavailable' }
    $privateValues=$privateJson | ConvertFrom-Json
    foreach($property in $privateValues.PSObject.Properties) { $runtimeValues[$property.Name]=$property.Value }
    foreach($key in $runtimeValues.Keys) {
        $previousValues[$key]=[Environment]::GetEnvironmentVariable($key,'Process')
        [Environment]::SetEnvironmentVariable($key,$runtimeValues[$key],'Process')
    }
    $listeners=@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if($listeners.Count -gt 0) {
        foreach($owner in ($listeners.OwningProcess | Select-Object -Unique)) {
            $process=Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
            if($process.CommandLine -notmatch [regex]::Escape($serveScript) -or
               $process.CommandLine -notmatch '--serve' -or
               $process.CommandLine -notmatch "--port\s+$Port(\s|$)") {
                throw "Port $Port is occupied by a different service (PID $owner)"
            }
        }
    }
    if($listeners.Count -gt 0) {
        $status=Invoke-RestMethod "http://127.0.0.1:$Port/api/ch09/flywheel/status" -TimeoutSec 10
        Write-Output "Reusing Ch09 http://127.0.0.1:$Port (flywheel pending: $($status.pending_count))"
        return
    }
    if($CheckOnly) {
        & $pythonPath -X utf8 $serveScript
    } else {
        # Preflight and uvicorn share one process: no second BGE/reranker load.
        & $pythonPath -X utf8 $serveScript --serve --port $Port
    }
    if($LASTEXITCODE -ne 0) { throw "Ch09 preflight/service exited with code $LASTEXITCODE" }
} finally {
    foreach($key in $previousValues.Keys) { [Environment]::SetEnvironmentVariable($key,$previousValues[$key],'Process') }
    Pop-Location
}
