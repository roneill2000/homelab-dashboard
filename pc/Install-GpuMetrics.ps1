# Installs the homelab GPU metrics endpoint on the gaming PC (run as an administrator).
# Copies gpu_metrics.py to C:\llm\gpu-metrics, registers a startup task (SYSTEM, hidden pythonw, restarts on failure),
# and opens TCP 9835 to the dashboard container (192.168.0.2) only.
param([string]$Python = (Get-Command pythonw.exe).Source)
$ErrorActionPreference = 'Stop'
$dir = 'C:\llm\gpu-metrics'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot 'gpu_metrics.py') $dir
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$dir\gpu_metrics.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Priority 7
Register-ScheduledTask -TaskName 'Homelab GPU metrics' -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Limited -Force | Out-Null
Get-NetFirewallRule -DisplayName 'Homelab GPU metrics' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Homelab GPU metrics' -Direction Inbound -Protocol TCP -LocalPort 9835 -RemoteAddress 192.168.0.2 -Action Allow -Profile Any | Out-Null
Start-ScheduledTask -TaskName 'Homelab GPU metrics'
Write-Output 'installed'
