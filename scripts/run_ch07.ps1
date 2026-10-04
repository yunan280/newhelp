param(
    [ValidateSet('default', 'demo')][string]$Profile = 'demo',
    [int]$Port = 9018,
    [string]$RouterCalibration = 'artifacts/ch07/20261004-native/router-calibration-02/router.json',
    [string]$ContextCalibration = 'artifacts/ch07/20261004-native/tokens-calibration-03/context-profile.json',
    [string]$PolicyCalibration = 'artifacts/ch06/ch06_20261002_01/policy-calibration-02/policy.json',
    [string]$MilvusCollection = 'ch06_eval_mysql_20261002_01'
)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$runtimeValues = @{
    CH06_PRIMARY_MODEL = 'deepseek-v4-pro'; CH06_CASCADE_ENABLED = 'false'
    CH06_CALIBRATION_PATH = (Resolve-Path -LiteralPath (Join-Path $repoPath $RouterCalibration)).Path
    CONTEXT_CALIBRATION_PATH = (Resolve-Path -LiteralPath (Join-Path $repoPath $ContextCalibration)).Path
    CH06_POLICY_CALIBRATION_PATH = (Resolve-Path -LiteralPath (Join-Path $repoPath $PolicyCalibration)).Path
    MILVUS_COLLECTION = $MilvusCollection
    CH05_CHECKPOINT_PATH = Join-Path $repoPath ".cache/ch07/$Profile/checkpoints.sqlite3"
}
$sixSettings = if ($Profile -eq 'demo') { @(18000, 2000, 2000, 3, 1200, 5) } else { @(128000, 4096, 4096, 4, 1200, 10) }
$names = @('MODEL_CONTEXT_WINDOW','MAX_OUTPUT_TOKENS','MAX_USER_INPUT_TOKENS','MAX_AGENT_STEPS','TOOL_RESULT_MAX_TOKENS','RERANK_TOP_K')
for ($index = 0; $index -lt $names.Count; $index++) { $runtimeValues[$names[$index]] = [string]$sixSettings[$index] }
$previousValues = @{}
Push-Location -LiteralPath $repoPath
try {
    foreach ($key in $runtimeValues.Keys) {
        $previousValues[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $runtimeValues[$key], 'Process')
    }
    & (Join-Path $repoPath '.venv-ch03/Scripts/python.exe') -X utf8 -m uvicorn mewhelp.main:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw "uvicorn exited with code $LASTEXITCODE" }
} finally {
    foreach ($key in $previousValues.Keys) { [Environment]::SetEnvironmentVariable($key, $previousValues[$key], 'Process') }
    Pop-Location
}
