# Gaming PC model server as a Windows service

- **Service** `llm-router` (WinSW v2.12.0, `C:\llm\service\llm-router.exe`, sha256 05B82D46…3A0DA): runs
  `llama-server.exe` with the same arguments as `C:\llm\start-llm-router.bat` (API key stays in that local file),
  starts at boot without a logon, restarts 10 s / 30 s after a crash. Logs in `C:\llm\service\logs`.
- **Game watch** (`LLM Game Watch` task, SYSTEM, every minute, priority 9): stops the service while any process runs
  from a game library (`steamapps\common`, Epic, EA, Origin, Ubisoft, GOG, Xbox) and starts it again afterwards.
  Add folders to `C:\llm\service\games-extra.txt`; add non-game tools found in those folders to `games-ignore.txt`.
  Log: `C:\llm\service\game-watch.log`.
- Replaced `Startup\LLM-Router.vbs` (moved to `C:\llm\disabled`), which had no restart. Install: `install-llm-service.ps1`,
  then `install-watch.ps1` (copies `game-watch.ps1`).
- Measured 22 Oct: idle server 215 MB RAM, 0.04% CPU, no VRAM (models unload after 5 idle min); watcher 1.8 s on one
  of 20 threads per minute. Tested with a fake game in a Steam library: stopped within the minute, resumed after.
