# Porting this homelab dashboard (guide for people and their coding agents)

This repo is one person's homelab, built for one exact set of machines. It is MIT-licensed: take what is useful. This file tells you (or your agents) what the pieces are, what is specific to the original setup, which design rules matter, and which traps cost the most time. It holds no secrets and no real addresses; hosts are named by role.

## 1. What is (and is not) in this repo

| In the repo | Not in the repo |
|---|---|
| React frontend (`src/`, `build.mjs`, Material 3 tokens `m3-*.mjs`, icons, fonts) → one self-contained `index.html` | **The core backend server** (`/usr/local/bin/homelab-dashboard.py` on the dashboard host). It embeds credentials, so it was never published. `deploy.py` patches it in place with `backend/backend_patch.py`. |
| Backend modules the server imports (`backend/*.py`: monitors, proxies, GPU broker) | Private config `/etc/homelab-dashboard/*` (schemas in section 4) |
| Services for other hosts (`alerts/`, `nightly/`, `updates/`, `pc/`, `systemd/`) and their installers | Network inventory, passwords, tokens, the ntfy topic |
| Tests (`tests/`, `alerts/test_*.py`), Playwright checks (`checks/`) | The tuned model files and engine configs on the GPU PC |

**Consequence:** a fork does not run as-is. Rebuild the core server against the API contract in section 3 (a small `http.server`/FastAPI app that imports the modules), or write your own collectors and keep only the frontend.

## 2. Architecture (roles, not addresses)

```
 phones / browsers ──► DASHBOARD HOST (LXC) :80  ── static index.html + /api/*
                         │  imports backend/*.py modules; SQLite metrics; 50 s fleet refresh
                         │  GPU broker (backend/gpu_broker.py) :8000 one URL for all models
                         ▼
   HYPERVISOR (Proxmox) ── pct/qm for guests, vzdump, syslog collector, homelab-alerts (alerts/)
   NAS (Unraid) ───────── storage/SMART/Docker, nightly log review on a small GPU (nightly/)
   DNS pair (Pi-hole ×2 + keepalived VIP) ── stats, blocking pause
   ROUTER (OpenWrt/GL.iNet) ── WAN status, DHCP, config backup
   GPU PC (Windows, consumer GPU) ── llama-server router :8080, Strata :8088/:8089, GPU metrics :9835,
                                     game-watch, forced-command SSH for sleep/wake
   CLIENTS of the broker: Open WebUI, an agent (Hermes), the dashboard Ask box, a reverse proxy (Caddy) page
   ALERTS: ntfy (one private topic), Uptime Kuma via a relay (alerts/kuma_ntfy_relay.py)
```

## 3. API contract the frontend expects (GET unless noted)

`/api/telemetry` (everything at a glance: wan, weather, pve, unraid, scrutiny, pihole, speedtest, syslog, netalertx, media, services, ip_matrix, ai) · `/api/storage?window=<s>` (throughput, latency, SMART, energy) · `/api/fleet` (guests + updates) · `/api/events` · `/api/updates` · `/api/widget` (compact status for the Android widget) · `/api/downloads[/search]` · `/api/media/poster/<id>` · `/api/qwen` (Ask box) · `/api/ai/live` (GPU live readings; calling it opens a 30 s fast-poll window) · `/api/maintenance` (alert snooze). State changes are `POST /api/action/<name>` and **must** be same-origin and carry header `X-Dashboard-Action: 1`. Run the frontend with `python3 scripts/preview.py` (proxies `/api/*` to a live backend and blocks every non-GET) and read `src/app.jsx` for the exact JSON fields each card uses.

## 4. Private config (create these yourself; fields only)

All under `/etc/homelab-dashboard/`, mode 0600, never committed:
- `ai.json`: `url` (llama-server base URL), `token` (its bearer token)
- `qwen.json`: `token` (shared secret for the NAS-side Ask box service)
- `energy.json`: `rate_per_kwh`, `rate_label` (optional; enables cost)
- `prowlarr.json`: `url`, `key` · `rdt.json`: `url`, `username`, `password` (download search/actions; optional)
- SSH keys + known_hosts for PC sleep (`pc_sleep_ed25519`, restricted by a forced command on the PC) and for updates (`update_ed25519`)

## 5. What to replace

- Hosts: `grep -rnE "192\.168\.0\.[0-9]+" --include=*.py --include=*.jsx --include=*.mjs --include=*.ps1 --include=*.sh .` (about 270 hits in 86 files). Best first step for a port: move every host/port into one config module and import it.
- PC paths: `grep -rn 'C:\\llm\|H:\\Strata'` (`pc/`, alert and AI code).
- Guest IDs and `pct`/`qm` calls (Proxmox-specific).
- Optional integrations; delete or stub what you do not run: Unraid, Pi-hole pair, GL.iNet router, Jellyfin, Sonarr/Prowlarr/RdtClient, speedtest-tracker, NetAlertX, Scrutiny, restic/Google Drive, Strata, the NAS GPU nightly review. Each card in `src/app.jsx` should hide when its block is missing.

## 6. Design rules (keep these; each one fixed a real problem)

1. **One GPU engine at a time.** Everything goes through the broker; it unloads the other engine before loading one, shares one file lock with the dashboard's engine switch, and never swaps mid-stream (waits up to 10 min). Clients pointing straight at the GPU PC caused half-filled caches and VRAM fights.
2. **The broker refuses** foreign `Origin`, unknown `Host` (DNS rebinding), non-JSON generation POSTs, and any client not on its allowlist (`CLIENTS`). A web page on any LAN device could otherwise force GPU swaps.
3. **Games win.** `pc/game-watch.ps1` stops llama-server and unloads Strata when a game runs; the broker answers 503 instead of loading. Detection is by process path (`games-extra.txt`, `games-ignore.txt`).
4. **Power actions are idempotent.** Wake checks SSH first; sleep joins a pending countdown and never sleeps if it was cancelled or is stale (a countdown surviving sleep used to re-sleep the PC right after wake).
5. **Alerts fail open and debounce.** A check must be bad twice in a row; missing data is never a recovery; snooze holds alerts and sends them afterwards if still bad; a recovery replaces its alert (ntfy `X-Sequence-ID`).
6. **Status reads are cheap.** `/api/storage` is a snapshot (~5 ms); slow collectors run in background loops; the PC GPU is polled only while someone is watching.
7. **Tests never trigger real actions.** The preview and Playwright checks abort every non-GET.

## 7. Traps that cost the most time

- **Windows VRAM:** CUDA could use only ~15.0 of 16.3 GB (WDDM reservation). Over budget, Windows silently moves memory to system RAM: the GPU shows ~100 % busy at ~85 W and speed collapses. Check `llama-server -lv 4` buffer sizes (`buffer size` lines); `llama-fit-params` gives projections but rejects `--spec-*` flags.
- **Hybrid Qwen models (Qwen3.5+ family):** each llama-server slot holds ~150-200 MB of recurrent state; the router picks 4 slots by default. `parallel = 1` freed enough for a bigger quant.
- **Speculative decoding (MTP) costs more than its head:** draft KV defaults to F16, and each extra draft token adds a recurrent-state copy. On a MoE that already decodes fast (Gemma 26B A4B at ~220 tok/s) the MTP drafter made it *slower*.
- **QAT quants** (quantization-aware trained) beat ordinary quants of the same size; prefer them when they exist.
- **Windows firewall "Query User" rules** created on first launch allow *any port from any address* for that program; they silently exposed model servers to the whole LAN. Replace them with explicit port + source-address rules and disable the auto ones.
- **Scheduled tasks** for model servers: run S4U (no console window to close by accident) at **priority 4**; the default 7 (BelowNormal) cut decode ~3×. Add restart-on-failure *and* end the start script with `exit $LASTEXITCODE`, or a crash never counts as a failure.
- **Killing by command-line pattern** (`pkill -f`, PowerShell `-match 'start-strata'`) also matches your own shell or a sibling server; kill by PID or an exact anchored pattern.
- **Strata packs** bind to their source GGUF shards by name and size only: a same-size updated shard needs a new pack in a new folder. Junction paths (`terminal\current`) fail from SSH/WMI with "untrusted mount point".
- **Pi-hole v6** rewrites `pihole.toml`: change settings with `pihole-FTL --config <key> <value>`, not by editing the file.
- **Bash `read` without `-r`** strips Windows backslashes from paths.

## 8. Tuning method for local models (the numbers will differ on your hardware)

1. New engine build or model in its **own folder**; the live one stays as rollback.
2. A/B on a private port (not the live router) with the live flags: short decode ×3, a ~35k-token prompt with a needle ×2, tool-call scenarios, a small graded maths/code set. Alternate A/B order to cancel drift.
3. **Fill test** before adopting any config near the VRAM limit: a prompt at ~95 % of the context window, then a short answer; speed must hold and the needle must be found.
4. Change one thing at a time; keep a backup of every config file with a dated suffix.
5. Reasoning budgets: measure how many thinking tokens answers actually use before capping; a cap only hurts the problems that need the length.
