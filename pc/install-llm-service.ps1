$ErrorActionPreference = 'Stop'
$dir = 'C:\llm\service'; New-Item -ItemType Directory -Force $dir, "$dir\logs" | Out-Null
$exe = "$dir\llm-router.exe"
if (-not (Test-Path $exe)) { Invoke-WebRequest -UseBasicParsing 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' -OutFile $exe }
'winsw sha256: ' + (Get-FileHash $exe -Algorithm SHA256).Hash
# Same arguments as start-llm-router.bat (the API key stays in local files only), plus a log.
$bat = Get-Content C:\llm\start-llm-router.bat -Raw
$args = (($bat -split "`n" | Where-Object { $_ -match 'llama-server.exe' }) -replace '^\s*llama-server\.exe\s*', '').Trim()
$xml = @"
<service>
  <id>llm-router</id>
  <name>LLM Router (llama.cpp :8080)</name>
  <description>Homelab model server. Always on; C:\llm\service\game-watch.ps1 stops it while a game runs.</description>
  <executable>C:\llm\llama.cpp\llama-server.exe</executable>
  <arguments>$([System.Security.SecurityElement]::Escape($args))</arguments>
  <workingdirectory>C:\llm\llama.cpp</workingdirectory>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>20 sec</stoptimeout>
  <logpath>$dir\logs</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
</service>
"@
Set-Content "$dir\llm-router.xml" $xml -Encoding UTF8
# Retire the interim scheduled task and stop the running copy.
if (Get-ScheduledTask 'LLM Router' -ErrorAction SilentlyContinue) { Stop-ScheduledTask 'LLM Router' -ErrorAction SilentlyContinue; Unregister-ScheduledTask 'LLM Router' -Confirm:$false; 'removed task LLM Router' }
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
if (-not (Get-Service llm-router -ErrorAction SilentlyContinue)) { & $exe install | Out-String }
Start-Service llm-router
Start-Sleep 5
'service: ' + (Get-Service llm-router).Status + ' / ' + (Get-CimInstance Win32_Service -Filter "Name='llm-router'").StartMode
