# Forced command for Homepage's PC-sleep key. SSH_ORIGINAL_COMMAND: check | sleep | force
$ErrorActionPreference = 'Stop'
$cmd = "$env:SSH_ORIGINAL_COMMAND".Trim()
$gpu = -1
try { $gpu = [int]((& nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) | Select-Object -First 1) } catch {}
function Out($o) { $o | ConvertTo-Json -Compress; exit }
if ($cmd -eq 'check') { Out @{ ok = $true; action = 'check'; gpu = $gpu } }
if ($cmd -notin @('sleep', 'force')) { Out @{ ok = $false; error = 'Unsupported command.' } }
if ($cmd -eq 'sleep' -and $gpu -ge 30) { Out @{ ok = $false; busy = $true; gpu = $gpu; error = "GPU busy ($gpu%)" } }
Remove-Item 'C:\llm\sleep.cancel' -ErrorAction SilentlyContinue
& msg.exe * /TIME:60 "Homelab: this PC will sleep in 60 seconds (requested from the dashboard). To cancel, run C:\llm\cancel-sleep.cmd" 2>$null
# Start the countdown outside this SSH session; Windows OpenSSH ends the session's child processes on disconnect.
$null = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\llm\homelab-sleep-now.ps1' }
Out @{ ok = $true; action = 'sleeping in 60 s'; gpu = $gpu }
