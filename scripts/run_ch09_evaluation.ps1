param([ValidatePattern('^[a-z][a-z0-9_]{0,63}$')][string]$RunId,
      [ValidateSet('手动','定时')][string]$TriggeredBy='手动',
      [switch]$Resume,
      [string]$BaseUrl='http://127.0.0.1:9030',
      [int]$TimeoutSeconds=7200)
$ErrorActionPreference='Stop'
try {
    $uri=[Uri]$BaseUrl
    if($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','localhost') -or
       $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
        throw 'Evaluation BaseUrl must be an explicit local HTTP service origin'
    }
    if(-not $RunId) { $RunId='ch09_sched_'+(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+([guid]::NewGuid().ToString('N').Substring(0,8)) }
    $repoPath=Split-Path -Parent $PSScriptRoot
    $logDir=Join-Path $repoPath 'log/ch09'
    [IO.Directory]::CreateDirectory($logDir) | Out-Null
    $logPath=Join-Path $logDir ($RunId+'.jsonl')
    $payload=@{run_id=$RunId; triggered_by=$TriggeredBy; resume=[bool]$Resume} | ConvertTo-Json -Compress
    $job=Invoke-RestMethod ($BaseUrl.TrimEnd('/')+'/api/ch09/evaluations') -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 30
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $json=$job | ConvertTo-Json -Depth 15 -Compress
        [IO.File]::AppendAllText($logPath,$json+"`n",[Text.UTF8Encoding]::new($false))
        Write-Output "$RunId $($job.status) $($job.processed)/$($job.dataset_size)"
        if($job.status -notin @('preparing','running')) { break }
        if((Get-Date) -gt $deadline) { throw "Polling timed out; task retained. Explicit -Resume uses the same RunId $RunId." }
        Start-Sleep -Seconds 5
        $job=Invoke-RestMethod ($BaseUrl.TrimEnd('/')+'/api/ch09/evaluations/'+$RunId) -TimeoutSec 30
    } while($true)
    if($job.status -ne 'completed' -or -not $job.eval_run_id) {
        throw "Evaluation did not complete cleanly: $($job.status); $($job.error)"
    }
    Write-Output ($job | ConvertTo-Json -Depth 15)
    exit 0
} catch {
    Write-Error $_
    exit 1
}
