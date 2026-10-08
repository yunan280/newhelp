param([int]$Port=9020,
      [int]$ModelContextWindow=32768,
      [string]$RouterCalibration='artifacts/ch08/20261007-01/router-calibration-05/router.json',
      [string]$ToolConfig='config/ch08-tools.json',
      [string]$PluginDir='tool_plugins',
      [string]$ContextCalibration='artifacts/ch07/20261004-native/tokens-calibration-03/context-profile.json',
      [string]$PolicyCalibration='artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json')
$ErrorActionPreference='Stop'
$repoPath=Split-Path -Parent $PSScriptRoot
$runtimeValues=@{
    CH06_PRIMARY_MODEL='deepseek-v4-pro'; CH06_CASCADE_ENABLED='false'
    CH06_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $RouterCalibration)).Path
    CH06_ROUTER_DATASET_PATH=Join-Path $repoPath 'eval/ch08/router'
    CONTEXT_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $ContextCalibration)).Path
    CH06_POLICY_CALIBRATION_PATH=(Resolve-Path -LiteralPath (Join-Path $repoPath $PolicyCalibration)).Path
    CH08_TOOL_CONFIG=(Resolve-Path -LiteralPath (Join-Path $repoPath $ToolConfig)).Path
    CH08_PLUGIN_DIR=Join-Path $repoPath $PluginDir
    MILVUS_COLLECTION='ch06_eval_mysql_20261002_01'
    CH05_CHECKPOINT_PATH=Join-Path $repoPath '.cache/ch08/checkpoints.sqlite3'
    MODEL_CONTEXT_WINDOW=[string]$ModelContextWindow; MAX_OUTPUT_TOKENS='2000'; MAX_USER_INPUT_TOKENS='2000'
    MAX_AGENT_STEPS='4'; TOOL_RESULT_MAX_TOKENS='1200'; RERANK_TOP_K='5'
}
$previousValues=@{}
Push-Location -LiteralPath $repoPath
try {
    foreach($key in $runtimeValues.Keys) {
        $previousValues[$key]=[Environment]::GetEnvironmentVariable($key,'Process')
        [Environment]::SetEnvironmentVariable($key,$runtimeValues[$key],'Process')
    }
    & (Join-Path $repoPath '.venv-ch03/Scripts/python.exe') -X utf8 -m uvicorn mewhelp.main:app --host 127.0.0.1 --port $Port --workers 1
    if($LASTEXITCODE -ne 0) { throw "uvicorn exited with code $LASTEXITCODE" }
} finally {
    foreach($key in $previousValues.Keys) { [Environment]::SetEnvironmentVariable($key,$previousValues[$key],'Process') }
    Pop-Location
}
