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
- **Models** (27 Oct 2026): GGUFs live on `H:\llm\models` (WD_BLACK SN850X); `C:\llm\models` is an NTFS junction to it
  so older paths still work. All four `models.ini` presets point at `H:\llm\models\...`: qwen3.8-27b, gemma-4-26b-a4b,
  qwen3.6-35b-moe, qwen3.8-27b-uncensored. The uncensored preset is `Qwen3.8-27B-Heretic-Ara-iq4_xs-3.0.gguf` (13.99 GB,
  no MTP draft layer): 51.38 tok/s over 1,024 tokens with ngram-mod (43% draft acceptance), 15,596 / 16,303 MiB VRAM,
  nothing spilled to system RAM. It replaced the 15.57 GB OrcaRouter IQ4_XS build, which exceeded 16 GB with all layers
  on GPU; Windows paged it over PCIe and prefill fell to 0.25 tok/s (ngram-mod with that file also stalled slot 0).
