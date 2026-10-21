# Sleep (not hibernate) after a 60 s warning unless C:\llm\sleep.cancel appears.
Start-Sleep -Seconds 60
if (Test-Path 'C:\llm\sleep.cancel') { Remove-Item 'C:\llm\sleep.cancel'; exit }
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false) | Out-Null
