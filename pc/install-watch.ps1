$ErrorActionPreference = 'Stop'
Copy-Item "$HOME\game-watch.ps1" C:\llm\service\game-watch.ps1 -Force
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\llm\service\game-watch.ps1'
$t1 = New-ScheduledTaskTrigger -AtStartup
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'LLM Game Watch' -Action $action -Trigger $t1, $t2 -Settings $settings -Principal $principal -Description 'Stops llm-router while a game runs; starts it again afterwards.' -Force | Out-Null
'watch task: ' + (Get-ScheduledTask 'LLM Game Watch').State
