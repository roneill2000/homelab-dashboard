# Sleep (not hibernate) after a 60 s warning unless C:\llm\sleep.cancel appears.
$start = Get-Date
Start-Sleep -Seconds 60
Remove-Item 'C:\llm\sleep.pending' -ErrorAction SilentlyContinue
if (Test-Path 'C:\llm\sleep.cancel') { Remove-Item 'C:\llm\sleep.cancel'; exit }
# Far more than 60 s passed: the PC slept (another way) during the countdown. Never sleep it again right after a wake.
if (((Get-Date) - $start).TotalSeconds -gt 90) { exit }
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false) | Out-Null
