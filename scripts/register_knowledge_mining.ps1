param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '02:00'
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'run_knowledge_mining.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId (
    [Security.Principal.WindowsIdentity]::GetCurrent().Name
) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName 'MewHelp-Ch03-KnowledgeMining' -Action $action `
    -Trigger $trigger -Settings $settings -Principal $principal -Force `
    -Description '每日抽取客服历史会话问答，并补齐 BGE-M3/Milvus 向量' | Out-Null
Get-ScheduledTask -TaskName 'MewHelp-Ch03-KnowledgeMining' |
    Select-Object TaskName, State, @{Name = 'NextRun'; Expression = {
        (Get-ScheduledTaskInfo -TaskName $_.TaskName).NextRunTime
    }}
