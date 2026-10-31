# Runs every minute as SYSTEM. Keeps llm-router running unless a game is running.
# A game = any process whose path is under a game library folder. Add folders or exe names to games-extra.txt,
# and non-games that live in those folders (tools, wallpaper apps) to games-ignore.txt, one per line.
$dir = 'C:\llm\service'; $flag = "$dir\paused-for-game.txt"; $log = "$dir\game-watch.log"
function Log($m) { Add-Content $log ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m) }
$roots = @('\\steamapps\\common\\', '\\Epic Games\\(?!Launcher\\)', '\\EA Games\\', '\\Origin Games\\', '\\Ubisoft Game Launcher\\games\\', '\\GOG Galaxy\\Games\\', '\\XboxGames\\')
if (Test-Path "$dir\games-extra.txt") { $roots += Get-Content "$dir\games-extra.txt" | Where-Object { $_.Trim() } | ForEach-Object { [regex]::Escape($_.Trim()) } }
$ignore = @('wallpaper32', 'wallpaper64', 'webwallpaper32', 'ui32', 'crashhandler', 'crashreporter', 'unitycrashhandler64', 'easyanticheat', 'easyanticheat_eos', 'vc_redist.x64', 'dxsetup')
if (Test-Path "$dir\games-ignore.txt") { $ignore += Get-Content "$dir\games-ignore.txt" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().ToLower() -replace '\.exe$', '' } }
$rx = '(?i)(' + ($roots -join '|') + ')'
$game = Get-Process | Where-Object { $_.Path -and $_.Path -match $rx -and ($ignore -notcontains $_.ProcessName.ToLower()) } | Select-Object -First 1
$svc = Get-Service llm-router -ErrorAction SilentlyContinue
if (-not $svc) { exit 0 }
if (Test-Path "$dir\restart.flag") {
    Remove-Item "$dir\restart.flag" -Force
    Restart-Service llm-router -Force
    Log "restarted llm-router via restart.flag"
    exit 0
}
if ($game) {
    if ($svc.Status -ne 'Stopped') { Stop-Service llm-router -Force; Set-Content $flag $game.Path; Log "paused for $($game.ProcessName) ($($game.Path))" }
    # Strata (H:\Strata, :8088) holds most of the GPU too; free it while a game runs. The GPU broker on LXC 104 will not
    # reload it while llama-server is stopped here.
    try { $h = Invoke-RestMethod http://127.0.0.1:8088/health -TimeoutSec 3; if ($h.loaded) { Invoke-RestMethod -Method Post http://127.0.0.1:8088/unload -ContentType 'application/json' -Body '{}' -TimeoutSec 60 | Out-Null; Log "unloaded Strata for $($game.ProcessName)" } } catch {}
} elseif ($svc.Status -ne 'Running') {
    Start-Service llm-router
    if (Test-Path $flag) { Remove-Item $flag; Log 'game closed; resumed' } else { Log 'service was stopped; started' }
}
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Get-Content $log -Tail 2000 | Set-Content "$log.tmp"; Move-Item "$log.tmp" $log -Force }
