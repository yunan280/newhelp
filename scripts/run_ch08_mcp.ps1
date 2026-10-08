param([Parameter(Mandatory)][ValidateSet('logistics','aftersales')][string]$Server,
      [int]$Port = 0)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
if ($Port -eq 0) { $Port = if ($Server -eq 'logistics') { 9021 } else { 9022 } }
Push-Location -LiteralPath $repoPath
try {
    & (Join-Path $repoPath '.venv-ch03/Scripts/python.exe') -X utf8 -m "mewhelp.ch08.mcp_servers.$Server" --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "MCP server exited with code $LASTEXITCODE" }
} finally { Pop-Location }
