param([ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')][string]$At='04:00',
      [string]$BaseUrl='http://127.0.0.1:9030')
$ErrorActionPreference='Stop'
if((Get-TimeZone).Id -ne 'China Standard Time') {
    throw 'Schedule is Asia/Shanghai. Set China Standard Time explicitly before registration.'
}
$scriptPath=Join-Path $PSScriptRoot 'run_ch09_evaluation.ps1'
$uri=[Uri]$BaseUrl
if($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1','localhost') -or
   $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
    throw 'BaseUrl must be a local HTTP origin'
}
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$scriptPath+'" -TriggeredBy 定时 -BaseUrl "'+$BaseUrl+'"'
) -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -WeeksInterval 1 -At $At
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'MewHelp-Ch09-Evaluation' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -Description 'Ch09 weekly frozen 40-case evaluation; requires the local Ch09 service' | Out-Null
$task=Get-ScheduledTask -TaskName 'MewHelp-Ch09-Evaluation'
$info=Get-ScheduledTaskInfo -TaskName $task.TaskName
[pscustomobject]@{ TaskName=$task.TaskName; Enabled=$task.Settings.Enabled; State=[string]$task.State;
                  NextRunTime=$info.NextRunTime.ToString('o'); TimeZone=(Get-TimeZone).Id;
                  Action=$task.Actions.Arguments; WorkingDirectory=$task.Actions.WorkingDirectory } | ConvertTo-Json
