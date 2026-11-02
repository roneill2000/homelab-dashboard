# Run on the Windows gaming PC in an elevated PowerShell.
# Enables OpenSSH Server with key-only login for Ryan's Mac, reachable from the LAN
# and the WireGuard range only. Safe to re-run.
$ErrorActionPreference = 'Stop'
$MacKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOR/vnHuWCnUoubeq/E3JqQDHw6fYcnQ4q4cGf3QfoU remote-mac'
$Allowed = '192.168.0.0/24', '10.0.0.0/24'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from PowerShell opened with "Run as administrator".'
}

# 1. Install and start the server.
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service sshd -StartupType Automatic
Start-Service sshd

# 2. Firewall: LAN and WireGuard only. The installer's broad rule is disabled, not deleted.
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
if (Get-NetFirewallRule -Name 'sshd-lan' -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name 'sshd-lan' -RemoteAddress $Allowed -Enabled True
} else {
    New-NetFirewallRule -Name 'sshd-lan' -DisplayName 'OpenSSH (LAN + WireGuard)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -RemoteAddress $Allowed | Out-Null
}

# 3. PowerShell as the SSH shell.
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null

# 4. Authorize the Mac key for administrators; Windows ignores the file unless only Administrators and SYSTEM can access it.
$keys = "$env:ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $keys)) { New-Item -ItemType File -Path $keys -Force | Out-Null }
if (-not (Select-String -Path $keys -SimpleMatch ($MacKey.Split(' ')[1]) -Quiet)) { Add-Content -Path $keys -Value $MacKey -Encoding ascii }
icacls $keys /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null

# 5. Key-only login. The original config is backed up once.
$config = "$env:ProgramData\ssh\sshd_config"
if (-not (Test-Path "$config.before-remote")) { Copy-Item $config "$config.before-remote" }
$text = Get-Content $config -Raw
foreach ($setting in 'PasswordAuthentication no', 'PubkeyAuthentication yes') {
    $name = $setting.Split(' ')[0]
    if ($text -match "(?m)^\s*#?\s*$name\b.*$") { $text = $text -replace "(?m)^\s*#?\s*$name\b.*$", $setting }
    else { $text = $setting + "`r`n" + $text }
}
Set-Content -Path $config -Value $text -Encoding ascii
& "$env:SystemRoot\System32\OpenSSH\sshd.exe" -t
if ($LASTEXITCODE -ne 0) { Copy-Item "$config.before-remote" $config -Force; throw 'sshd_config failed validation; original restored.' }
Restart-Service sshd

Write-Host "Done. From the Mac: ssh $env:USERNAME@192.168.0.12 hostname"
Write-Host "Undo: Stop-Service sshd; Set-Service sshd -StartupType Disabled; Remove-NetFirewallRule -Name sshd-lan"
