# Homelab dashboard

Custom React dashboard for http://192.168.0.2/. Dark control room with service search, browser-local favorites, responsive navigation, live host telemetry, and disk history. Built 19 October 2026.

## Build and verify

```sh
npm ci
npm run build
python3 -m unittest -v test_storage.py test_backend_patch.py test_activity.py
npx playwright install chromium
node ui-smoke.mjs
# Or use already installed Chrome in an isolated temporary profile:
PW_CHANNEL=chrome node ui-smoke.mjs
```

`ui-smoke.mjs` uses an isolated Playwright browser against the live dashboard. All action POST requests are intercepted and aborted; DNS and log settings are never changed by the test. Screenshots are local in ignored `.preview/`.

`python3 preview.py` serves a localhost-only preview on port 8799. It proxies existing telemetry, maintains its own ignored disk-history database, and refuses POST actions.

## Deployment

Homepage is Proxmox LXC 104. Existing Python service `homelab-dashboard` listens on port 80. Its secret-bearing integrations remain on the server and are never pulled into this project.

`python3 deploy.py` uploads the built HTML and storage collector through SSH to Proxmox. It patches the HTML assignment, storage import/GET route, and bounded collector accuracy fixes through `backend_patch.py`; credentials remain in remote memory and existing control actions are preserved. It checks syntax, creates a private timestamped backup, restarts only the dashboard service, checks its API, and restores the previous files if its health check fails.

Remote files:
- `/usr/local/bin/homelab-dashboard.py`: existing service, minimally patched.
- `/usr/local/bin/storage_monitor.py`: read-only collector.
- `/opt/homelab-dashboard/index.html`: compiled React page; React runtime remains locally hosted in `/static/`.
- `/var/lib/homelab-dashboard/metrics.sqlite3`: rolling 24-hour SQLite history, survives service restarts.
- `/opt/homelab-dashboard/backups/20261019T172419592725Z`: original deployment rollback copy. Keep it private: the backend includes existing credentials.

To restore the original page, inside LXC 104 copy `homelab-dashboard.py` from that backup to `/usr/local/bin/homelab-dashboard.py`, then `systemctl restart homelab-dashboard`. Keep the history database. For later backups, restore the files listed as existing in that backup's `manifest.json` as well.

## Storage definitions

Both hosts already expose Glances v4 at port 61208. The existing monitors now run the bounded `host_probe.py` through Glances AMP. No new listening port is added. The collector reads `diskio`, `fs` and `amps`, subtracts cumulative byte counters, and stores real samples; absent/reset counters are null, while verified unchanged counters produce zero. Sampling waits 10 seconds after each collection, so actual intervals include network time. Larger windows average readings to approximately 240 points; charts preserve gaps.

| Card | Host | Device | Filesystem |
| --- | --- | --- | --- |
| Proxmox scratch | .3 | sda | `/mnt/share`, SMB `n100` |
| Unraid array | .4 | sdc | `/mnt/disk1` (Glances `/rootfs/mnt/disk1`) |
| Unraid cache | .4 | sdd | `/mnt/cache` (Glances `/rootfs/mnt/cache`) |
| Unraid parity | .4 | sdb | No filesystem capacity |

Mappings were checked against Proxmox mounts and Unraid's `/var/local/emhttp/disks.ini`. Recheck after replacing or adding disks. The current Unraid array has one data disk; this is not automatic multi-disk discovery. Parity throughput is separate and never added to data throughput. Speeds use decimal MB/s; capacity uses decimal GB/TB. These are physical disk rates, not SMB network transfer rates. Busy percentage, average completion latency and average queue depth come from cumulative Linux disk counters via the AMP probe. Latency is unknown when no operations complete, while confirmed idle busy/queue readings are zero. Reboots, resets and gaps over 90s invalidate deltas.

History began at deployment, 19 October 2026 at 13:24 EDT. There is no historical backfill. The combined SABnzbd/RdtClient/scratch timeline is now persisted server-side for 24h. Its history began at 13:57 EDT on 19 October; old disk-only samples remain intact. There is no backfill for the new metrics. Host and application collectors now expose per-source freshness and preserve missing readings as unknown. Services without direct monitoring are labeled unmonitored.

## Validation

Eighteen Python tests cover cumulative rates, counter resets, missing-vs-idle readings, persistent history and outage gaps, parity separation, and backend accuracy transformations. Live API checks passed for all four windows and invalid input. A dashboard-service restart preserved recorded history. UI smoke checks are recorded in the session handoff.

`baseline.html` preserves the original public HTML with initial live telemetry stripped. `src/` is the editable frontend; `index.html` is generated.

## Personal layout revision

Ryan requested removal of decorative copy and better phone/large-screen use. Page subtitles, taglines, and onboarding copy are removed. Metric definitions are collapsed under “Metric details”. Phone controls use 44px targets; charts have larger axis labels; the fleet table becomes cards. Larger screens use a wider content area, four-column storage/service grids and drive-health cards.

`DASHBOARD_URL=http://192.168.0.2/ node responsive-check.mjs` checks Overview, Storage, Services and Fleet at 360×800, 412×915, 448×998, 998×448, 1440×1000, 1920×1080, 2560×1440 and 3440×1440. It checks both page and card overflow, mobile topbar target sizes and JavaScript errors, and captures phone/desktop/ultrawide screenshots. The mobile contexts use touch and DPR 3. The 448×998 case approximates the Pixel 11 Pro XL panel's 1344×2992 aspect ratio, per [Google's specifications](https://store.google.com/us/product/pixel_11_pro_specs?hl=en-US); Android display-size settings and browser chrome can change the actual CSS viewport. This is browser emulation, not an on-device test.

## Larger type and independent review

See `AUDIT-2026-10-19.md` for three Sol agents' findings, fixes, remaining limits and recommendations. Additional regression checks:

```sh
python3 -m unittest -v test_storage.py test_backend_patch.py
DASHBOARD_URL=http://192.168.0.2/ node accessibility-check.mjs
DASHBOARD_URL=http://192.168.0.2/ node content-check.mjs
```

`content-check.mjs` injects synthetic failure responses in its isolated browser only. It does not modify server telemetry. The pre-audit rollback is `/opt/homelab-dashboard/backups/20261019T174131926226Z`; restore backend, HTML and storage_monitor.py together from its manifest if reverting the audit changes.


## Download activity and recovery

`activity_monitor.py` adds an `activity` table to the same SQLite database. It samples SAB's existing telemetry (reported KiB/s converted to bytes/s), RdtClient's authenticated `/api/v2/transfer/info` local download rate (bytes/s), scratch byte throughput, busy percentage, average I/O completion latency, queue depth and Proxmox PSI `some.avg10`. Charts use decimal MB/s consistently. The sources are polled close together; they are not transactionally synchronized. Longer ranges average samples. Correlation does not establish a bottleneck.

`configure_activity.py` reuses the Rdt login already stored in Sonarr, transferring it inside the home network directly to root-only `/etc/homelab-dashboard/rdt.json` in LXC104. Credentials never enter this checkout or the browser. Local preview intentionally has no Rdt credential; it shows unavailable unless testing against the deployed API.

`install_probes.py` installs the source as a Glances AMP command at 10s refresh. On Proxmox it is `/etc/glances/homelab-host-probe.py`; on Unraid it lives in `/mnt/user/appdata/glances/` and executes inside Glances against the existing read-only `/rootfs` mount. Only Glances is restarted during installation. The script backs up each prior configuration, verifies the endpoint, and restores on failure. Original pre-probe backups are `/etc/glances/homelab-backup-20261019T175330Z` and `/mnt/user/appdata/glances/homelab-backup-20261019T175338Z`. Restore `glances.conf` from the first on Proxmox, remove the newly created `glances.conf` on Unraid (none existed before), and restart the respective monitor to remove the AMP; the source file can remain unused. Reinstallation accepts only the exact expected AMP section.

Recovery uses bounded Proxmox backup task queries (20 archived and 20 active, cached 60s). It distinguishes latest job result from last successful job. Enabled-job membership is checked separately against the guest inventory (added 20 October); membership does not prove a successful archive or restore. Unraid live `var.ini` supplies operation state and completion timestamps; assigned devices come from `disks.ini`, excluding empty slots. Completed means idle, exit 0 and a valid finish timestamp at/after start. An active/paused operation never displays a completion result. Source failures become unknown, never healthy.

Validation includes `node activity-check.mjs` for failed vs successful backups, paused parity, stale sources and zero vs missing rates. Sol review checked 48 chart-mode/view/viewport combinations at 360,448,1920,3440px. Eighteen Python tests and live smoke/accessibility/content checks passed. No download, backup, parity or DNS operations were triggered.

Integration sources: [Glances AMP](https://glances.readthedocs.io/en/latest/aoa/amps.html), [RdtClient transfer implementation](https://github.com/rogerfar/rdt-client/blob/main/server/RdtClient.Service/Services/QBittorrent.cs), [Unraid operation interpretation](https://github.com/unraid/webgui/blob/master/emhttp/plugins/dynamix/ArrayOperation.page), [Proxmox task query defaults](https://github.com/proxmox/pve-docs/blob/master/generated/pvenode.1-synopsis.adoc).

Dashboard backup before these integrations: `/opt/homelab-dashboard/backups/20261019T175724956203Z`. Restore files in its manifest to revert the dashboard and retain the SQLite database; this does not remove the separately installed Glances probes or Rdt credential file.

## Full log entries

The syslog monitor's `/api/status` truncates `last_alert` to 95 characters. `patch_log_details` now fetches `/api/alerts` before the dashboard state lock, with a 3s timeout and 256KiB response cap, and selects the newest valid event. It preserves the original summary if detail retrieval fails and explicitly labels that fallback. Event time, host and severity are separate metadata; the raw message remains intact. Entries over 300 characters offer an accessible inline expand/collapse control. Normal entries wrap in full. `test_log_details.py` covers bounded retrieval, fallback, ordering and patch idempotence; `node log-check.mjs` verifies the live full message and long-line wrapping at 360/448/1920px. Pre-change rollback: `/opt/homelab-dashboard/backups/20261019T180339701156Z`.

## Repeated logs and current service state

`log_monitor.py` groups the returned recent alert window by host, severity and exact message after stripping transport timestamps/host prefixes and process PIDs. Exit codes, paths and other details remain significant. The current endpoint returns 60 entries; these counts are explicitly separate from the monitor's 150-warning daily total. First/last times cover the returned window only. The latest raw message remains expandable.

A fixed, read-only Proxmox probe checks `filebrowser.service` inside utilities LXC 102 every 30s through the existing Glances AMP. No log-supplied value can choose a command. The frontend says “Running again” only when verified active/running state has a start time after the group's last failure. Other states include running, failed, restarting, stopped and unverified. Service observations expire after 90s; source failures retain historical groups with unverified current status. Only utilities/FileBrowser has this explicit mapping so far. Process state is not proof of complete application health.

Deployed probe backup: `/etc/glances/homelab-backup-20261019T181542Z`. Initial grouped-view dashboard backup: `/opt/homelab-dashboard/backups/20261019T181654062392Z`. 29 Python tests pass; `log-groups-check.mjs` verifies current recovery, stale/failed states, scoped counts, keyboard expansion and 360/448/1920/3440px layouts.

## Cache protection and fill forecast

`cache_guard.py` runs on Unraid every five minutes, starts the stock mover at ≥70% cache usage, and skips when mover is already active or the array/cache is unavailable. It preserves the existing 03:40 daily mover job. Persistent cron/source are in `/boot/config/plugins/dynamix/homelab-cache-guard.{cron,py}`; runtime state and bounded mover output are under `/var/run` and `/var/log`. `install_cache_policy.py` installs this with backups. Unraid's `update_cron` has a malformed shebang on this host, so installers explicitly invoke Bash.

Ryan authorized and completed the maintenance window on 20 October 2026. **The persistent and effective cache reserve are now 104,857,600 KiB (100 GiB).** The array is STARTED and all six previously running containers were restored. The existing five-minute cache guard and daily mover remain enabled. `python3 apply_cache_reserve.py` is read-only; `--apply` refuses unless the array is already stopped and uses Unraid's own setting handler. `maintain_cache_reserve.py` is an explicit maintenance script, not a scheduled job; any future array interruption needs authorization.

`storage_monitor.py` now stores free/total bytes in a separate 24-hour `capacity_samples` table. The cache card estimates time to full from net free-space change, not device write throughput. It requires two minutes of uninterrupted observations, checks the latest minute for direction, and uses the faster positive rate from that window and up to five minutes. This avoids hiding new filling behind an earlier large mover release. A missing/stale source, collection gap or resized pool resets confidence. Flat/decreasing usage has no numeric ETA. Under 16 MiB free is explicitly “Cache full.” Bursty and concurrent writes can outrun the estimate.

`CACHE-RCA-2026-10-19.md` records the full-cache evidence. `test_cache_policy.py` covers the gate, rate direction, outages and a mover-release/renewed-growth regression. `cache-forecast-check.mjs` covers live rendering and synthetic filling/flat/draining/full/stale states at 360/448/1920/3440px. Browser checks use an isolated Chrome process and intercept all dashboard action routes.

## Nightly local log review

`nightly/runner.py` runs on Unraid, takes a bounded 24-hour retained-log snapshot, deduplicates matching warning/critical patterns and requests short explanations for up to eight groups. It does not discover arbitrary new event types outside the existing monitor patterns. Counts, timestamps, missing coverage and independently measured FileBrowser state are shown separately from model interpretations. Interpretations are suggestions, not verified root causes. An explanation that omits a numeric exit status explicitly present in the source is withheld and labeled; the source facts remain available. No generated command is executed.

The runner and reports live at `/mnt/disk1/.homelab-nightly`; model files live at `/mnt/disk1/.homelab-nightly-models`, both private array directories. Model files are mounted read-only, with no automatic downloads during inference. The container is restricted to the Arc render node, two CPU cores, a 5 GiB system-memory limit, one sequence and a bounded 2,048-token context. The host reports no swap-limit capability. API binding is localhost only. The dedicated container stops after success/failure, releasing GPU memory. Reports are bounded to 64 KiB and 30 archived runs. A lock prevents overlapping jobs.

Schedule installation is `python3 nightly/install_runner.py --schedule`. At 03:00 America/New_York the job gives backups 90 seconds to publish their active state, then waits up to 90 minutes for fresh idle backup state and at least 1 GiB free cache. Model load is bounded to fifteen minutes, with immediate failure if the container exits; each group gets 180 seconds. Reasoning is capped at 768 tokens within a 1,152-token total response limit; only the final explanation is published. Failure produces a failed report, not a healthy-state claim. The dashboard marks reports over 36 hours old and runs without progress for over two hours.

`nightly/install_model.py` installs the pinned Qwen3.5-4B Q4_K_M model and llama.cpp Vulkan runtime. Model downloads use an exact Hugging Face revision and verified SHA-256; Docker uses an exact image digest. Scheduled runs require the image to exist locally. `nightly/validate_model.py` exercises synthetic service failure, wireless link-down, full-disk, out-of-memory, benign completion and log-instruction cases. Answers need human review; generating valid text is not an accuracy score.

`nightly/install_snapshot.py` adds a bounded read-only endpoint to the existing Proxmox syslog monitor. It reads up to 32 files, tails at most 16 MiB per file and emits up to 80 groups. This avoids the monitor's 150-event memory buffer and 60-event recent endpoint, neither of which is a full daily count. Installing this endpoint restarted that monitor and reset its in-memory ring; retained log files were preserved. Snapshot coverage limits and unknown timestamps are explicit.

`nightly-live-check.mjs` checks an actual fresh successful Qwen report on phone, desktop and ultrawide. `nightly-check.mjs` checks report wrapping, escaped model text, measured historical service state, limited scope, failure and stale-run states. `test_nightly.py` checks bounded snapshot/redaction, evidence mapping, backup freshness, private report retention, local-only model loading and container cleanup on failure. See `LOCAL-LLM-NOTES.md` for hardware/model findings.

To disable future nightly reviews, remove only `/boot/config/plugins/dynamix/homelab-nightly.cron` and run `/bin/bash /usr/local/sbin/update_cron` on Unraid. To disable only the usage-triggered mover, remove `homelab-cache-guard.cron` from that same directory and rebuild cron; the original 03:40 stock mover remains. Existing reports and model files can stay on the array. Neither change requires an array restart.

Final 19 October checks: 47 Python tests cover collectors, snapshot bounds, report validation and container cleanup. The real-log run completed in 398.6 seconds, with one explanation withheld after validation; the 03:00 schedule is enabled. Current dashboard backup: `/opt/homelab-dashboard/backups/20261019T191724244092Z`. Warm model loading was ~24s; cold loading during active array traffic took ~184s. See `LOCAL-LLM-NOTES.md` for measured limits and the pending first scheduled execution.

## Ask Qwen

Overview has a single-question prompt bar for local Qwen3.5-4B. As of 20 October it retrieves question-selected operational observations: host metrics and guests/containers, service reachability/inventory, disks and history, SMART, backup/parity, download rates/queues, aggregate DNS/network data, AI/GPU state, and bounded retained warning logs. The mapped FileBrowser service check distinguishes historical warnings from measured current state. General knowledge answers are allowed. It has no conversation history, internet browsing, private-note retrieval or arbitrary execution tools. Answers show the selected sources, omissions and observation time. The latest question/answer is held only in broker memory and expires after 15 minutes; browser input stays in that tab.

`nightly/qwen_service.py` is a lightweight authenticated broker at Unraid `192.168.0.4:11438`. Only the dashboard and local host addresses are accepted. `qwen_proxy.py` keeps its token in a root-only file on LXC104; the browser never receives it. Submission requires a same-origin request with a custom header. The existing dashboard remains a private LAN application with no user login. Model inference itself still binds only to Unraid localhost.

The broker and nightly runner share `run.lock` for the whole GPU session. A busy nightly job rejects prompts; the scheduled job waits up to 20 minutes if a question is running. Each question checks fresh backup-idle state and ≥1 GiB cache free before loading and again before generating. It refreshes measurements after loading, uses the model's tokenizer to cap input at 2,750 tokens within a 4,096-token on-demand context (the separate nightly job retains 2,048), and caps generation at 1,152 tokens including reasoning. Questions are limited to 1,000 characters and can still require shortening to fit the token budget. No partial/reasoning output is published.

`python3 nightly/install_qwen.py` installs the broker and shared runner with private backups and remote credentials. Installation takes the GPU lock and refuses while a model job is active. Persistent `/boot/config/plugins/dynamix/homelab-qwen.cron` checks the singleton broker every minute; this starts only its lightweight manager, not the model. The container starts on a submitted question and is stopped and verified before the answer becomes complete. `stop_model()` attempts forced removal of only the dedicated model container if normal shutdown cannot be verified. The nightly report also publishes success only after verified cleanup.

Validation: 65 offline Python tests, `node qwen-check.mjs` (intercepted requests, timestamp/expiry/error/security/layout fixtures), existing UI smoke and live nightly checks. A real question submitted through the phone-sized homepage correctly returned array usage 66.4% and free space 1,345.5 GB from its captured readings. Its model container was removed afterward and all six existing Docker apps remained running. Final deployed runner/broker/scrubber hashes matched local sources. The minor parity-label and pre-success cleanup refinements were covered offline after that real question; no extra expensive inference was needed.

Final prompt deployment backup: `/opt/homelab-dashboard/backups/20261019T200332573559Z`; broker/runner backup: `/mnt/disk1/.homelab-nightly/backups/20261019T200325209723Z`. Snapshot redaction deployment backup: `/usr/local/bin/homelab-syslog-backup-20261019T200109Z`. That monitor restart reset its in-memory alert ring; retained files were preserved. See `AUDIT-QWEN-2026-10-19.md` for the Sol review and remaining limits.

To disable on-demand prompts, remove only `/boot/config/plugins/dynamix/homelab-qwen.cron`, rebuild cron with `/bin/bash /usr/local/sbin/update_cron`, and terminate only the idle `qwen_service.py` manager. Do not kill a running question or remove the shared nightly files. This does not disable the nightly review. Restore the dashboard backup manifest if removing the prompt UI/proxy as well; keep credentials private.

## Arc A310 view and Qwen performance

The **Arc A310** navigation view shows separate compute, render, video, copy and video-enhancement engine activity, device-wide VRAM usage, GPU temperature, clock and fan speed. Engine, VRAM and temperature charts use the existing 15m/1h/6h/24h controls, with 24-hour SQLite retention. GPU history began at 16:32 EDT on 19 October 2026; there is no backfill. Percent charts use a fixed 0–100 scale; VRAM uses the observed usable-memory limit.

`nightly/gpu_probe.py` runs a small monitor thread in the existing broker. It verifies Intel PCI ID 8086:56a6 at 0000:03:00.0 and explicitly selects that device with `intel_gpu_top`. It takes a roughly one-second engine sample every ten seconds. It handles this installed tool's adjacent JSON objects and discards its short initialization sample. Short bursts between samples can be missed. Engine classes are not summed into a single utilization number. In the live Qwen test, Vulkan inference activity appeared on Render rather than Compute. No inference or other GPU workload is started by monitoring.

VRAM comes from the A310's `i915_gem_objects` local-memory section; the measured usable limit is 4,048 MiB, distinct from system RAM and the reserved area. Temperature/fan come from that PCI device's hwmon files. Unavailable fields remain null; stale observations over 30 seconds are withheld. `gpu_monitor.py` retrieves the private authenticated `/gpu` broker endpoint and stores samples in a separate `gpu_samples` table in the existing database. Collection gaps remain visible. Disabling the Qwen broker also disables this GPU source.

The Qwen bar shows current A310 compute/render/VRAM/temperature readings and elapsed request time. After completion it shows measured tokens/s, input/output tokens, model-load time, generation time and total request time. Speed and generation duration come directly from llama.cpp's `timings.predicted_per_second` and `predicted_ms`, not from visible words or loading time. Generated token counts include internal reasoning; reasoning text is not exposed. If the server omits timing fields, the UI shows unknown. Total time includes context preparation and verified cleanup and can exceed load plus generation.

Live validation: 16.22 tokens/s, 452 input tokens, 819 output tokens, 6.53s warm model load, 50.43s generation, 62.84s total. During inference Render was about 97.3–97.7% and VRAM was 2,825 MiB. After cleanup all engines returned to zero and VRAM to 18 MiB; all six existing application containers remained running. This is one measured request, not a performance guarantee. A dashboard restart interrupted the first browser polling test; the broker completed that same job and a new read-only browser check verified its displayed metrics without another inference request.

Validation: 71 offline Python tests, `node gpu-check.mjs`, updated `node qwen-check.mjs`, and existing dashboard smoke. GPU checks cover all graph modes/ranges, keyboard inspection, stale/partial readings, phone 360/448 and desktop 1920/3440 widths. GPU source and broker backup: `/mnt/disk1/.homelab-nightly/backups/20261019T203240703902Z`. Dashboard backup: `/opt/homelab-dashboard/backups/20261019T203358037891Z`. No array or existing application-container restart was required.

Runtime reference for token statistics: [llama.cpp server timing fields](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#timings-and-context-usage). GPU command arguments were checked against the installed `intel_gpu_top -h` and actual bounded output on this host.

## Open WebUI and desktop models

Open WebUI at `http://192.168.0.16:3000` is the launch destination on Overview, in the Local AI view, and in the searchable/pinnable service catalog. The Local AI view shows the three model IDs reported by the Windows llama.cpp router, current load state, configured/runtime context and model-specific Open WebUI links. Unloaded models say “Loads on demand.” Router connection details are collapsed; backend ports are not service tiles. Ryan confirmed the single-router setup and requested that Open WebUI remain the user-facing entry point.

Recorded benchmark speeds supplied by Ryan on 19 October are 90.0 tok/s for qwen2.5-coder-14b, 86.1 for qwen3.6-35b-moe and 16.3 for qwen3.8-27b. They are explicitly labeled previous benchmarks, not live telemetry or measurements from this session. The one-model-at-a-time behavior is also from Ryan's supplied configuration summary.

`ai_monitor.py` polls Open WebUI `/api/config` and authenticated `http://192.168.0.12:8080/v1/models` about every 30 seconds. Failed reads retain the last inventory but mark it unavailable; stale load states become unverified. Only model ID, state and context allocation are exposed. Context uses loaded `meta.n_ctx` or configured `--ctx-size`, never the larger training-context limit. No chat, load or unload request is issued by this collector.

`python3 configure_ai.py` reuses the verified Open WebUI connection credential entirely on the home hosts. It writes `/etc/homelab-dashboard/ai.json` in LXC104 with mode 0600. Secrets, model paths and process arguments are not sent to the browser or saved in this repository. `deploy.py` installs the collector along with the existing dashboard modules.

Validation: 76 Python tests; isolated Chrome `ai-check.mjs` and existing `ui-smoke.mjs`. UI checks cover live inventory, model-link encoding, search, unavailable states and 360/448/1920/3440px layouts without submitting prompts. Direct workstation LAN HTTP was unavailable, so browser verification used a temporary localhost SSH forward to the same deployed Homepage. Signed-in model selection inside Open WebUI was not exercised. Link syntax follows [Open WebUI URL parameters](https://docs.openwebui.com/features/chat-conversations/chat-features/url-params/).

Final deployment backup: `/opt/homelab-dashboard/backups/20261019T223810596445Z` (previous working AI layout). The pre-integration backup is `20261019T223345758535Z`. No model process, array or existing application container was restarted.

### Gaming PC off or asleep

The Windows PC is Ryan's gaming machine and is intentionally offline between uses. Homepage treats its unreachable model API as a normal unavailable state, with a neutral note rather than a host-failure alarm. Open WebUI availability is independent; its launcher remains accessible. Desktop-model chat links are disabled while the model source is unavailable/stale or Open WebUI is unavailable. They return automatically after fresh successful reads. The collector uses four-second HTTP timeouts and checks about every thirty seconds; the UI observes these on its normal refresh cycle. It sends no wake, model-load or inference commands.

If Homepage restarts while the PC is off, the three saved model profiles and recorded benchmarks remain visible with unavailable state and unknown context. Cached readings, when present, are labeled last seen. The separate A310 Homepage prompt remains on Unraid; there is no automatic fallback of Open WebUI requests to that model. Open WebUI itself cannot generate desktop-model answers while the PC is off.

Validation: six collector tests, including independent WebUI availability, cold start and recovery; browser fixtures for offline, cold start, stale status, return online and WebUI outage. Phone/desktop/ultrawide checks and existing UI smoke pass. The real gaming PC was not powered off for these tests. Dashboard backup: `/opt/homelab-dashboard/backups/20261019T224002187728Z`.

### Desktop model configuration audit — 19 October 2026

Read-only audit of authenticated router `/v1/models`, `/props`, and the loaded 27B model's `/props?model=qwen3.8-27b&autoload=false` and `/slots?model=qwen3.8-27b&autoload=false`, plus selected Open WebUI SQLite configuration/model fields (database opened mode=ro). No settings changed, models switched, prompts submitted or services restarted. A slot was processing an existing request, so no inference validation was started.

Confirmed router b11742-4c3c5810f, `max_instances=1`, autoload enabled, Open WebUI API base `.12:8080/v1`, Ollama integration disabled. All three launch presets use Jinja, Flash Attention, q8_0 K/V caches and20 CPU threads. Coder14B uses Q4_K_M,32768 context, GPU layers99 (all-layer request), temperature0.7/top_p0.8/min_p0.05. MoE35B uses UD-Q4_K_M,16384 context, fit-target1024, temperature0.7/top_p0.8/min_p0.05 and no explicit top_k. 27B uses UD-Q4_K_XL,32768 context and50 requested GPU layers. Its loaded properties confirm temperature1.0/top_p0.95/top_k20/min_p0/repeat_penalty1/presence_penalty0, matching Qwen thinking recommendations. Four slots each report32768 context; one was active. Actual offload allocation for the two unloaded models was not verified.

Findings: the MoE sampling preset does not match [Qwen's thinking-mode recommendations](https://huggingface.co/Qwen/Qwen3.6-35B-A3B): for general thinking, temperature1/top_p0.95/top_k20/min_p0/presence_penalty1.5/repetition_penalty1. This is a tuning mismatch, not proof of broken answers. Open WebUI has a persisted27B `reasoning_effort=xhigh` override, while its router launch default is medium; xhigh is valid but chooses deeper reasoning ([Qwen guidance](https://huggingface.co/Qwen/Qwen3.8-27B)). No other model overrides or global default sampling parameters were present. Per-chat overrides were not inspected.

No `--sleep-idle-seconds` option appeared in any reported launch arguments. [llama.cpp documents idle sleep as disabled by default](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#sleeping-on-idle). Recommend an explicit roughly5-minute idle unload for a shared gaming GPU; one-model capacity alone does not guarantee idle VRAM release. Host-level environment/preset files were not read, so absence from reported arguments is not proof no external idle policy exists. The27B instance was awake and active during this read.

Open WebUI title/tags/follow-up generation are enabled, with empty task-model/default parameter overrides. Its HTTP client timeout is600s. Very long xhigh requests could reach that timeout; no such failure was observed. The loaded27B server reports vision/video/audio false, so current serving is text-only despite upstream model multimodality. These are limits to be aware of, not changes made.

### Approved model defaults — 19 October 2026

Ryan authorized medium reasoning and the proposed sampling/idle changes, and chose a PowerShell command for Windows configuration because SSH/SMB/WinRM management ports were unreachable from LXC104. `configure_webui_models.py` uses the installed Open WebUI model persistence methods to update 27B `reasoning_effort=medium` and create the MoE override with temperature1.0, top_p0.95, top_k20, min_p0, presence_penalty1.5 and repeat_penalty1. Existing model metadata/access are preserved, and Open WebUI model history records the changes. Private SQLite backup: `/opt/open-webui/backups/20261019T224642Z/webui.db`.

Both persisted records and the installed `apply_model_params_to_body_openai` request builder were checked successfully. Normal chat handling reads model parameters from the database per request, so no application restart was necessary. Explicit per-chat parameters can still override defaults. No new inference or model restart was used for verification.

`Set-LlmDefaults.ps1` is staged at `\\192.168.0.4\4TB\homelab\Set-LlmDefaults-20261019.ps1` (Unraid `/mnt/user/4TB/homelab/Set-LlmDefaults-20261019.ps1`). SHA256: `967b4a9e16c59d4979fe5897cbb2d8533cab42d3bbb0aef0f39cf24f2feb08f5`. It validates all three expected INI sections, preserves unrelated settings, atomically replaces `C:\llm\models.ini` with a timestamped backup, and verifies the resulting file. It adds sleep-idle-seconds300 to each model, aligns native MoE sampling, and sets native27B medium. It does not stop/reload the router. Runtime changes require Ryan to run the command and then restart his router after any active chat finishes. Script source was reviewed against official llama.cpp preset syntax; it has not been executed on Windows.

Run in PowerShell on .12:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\192.168.0.4\4TB\homelab\Set-LlmDefaults-20261019.ps1"
```

The execution-policy override applies only to that PowerShell process. After Ryan runs it and restarts the router, re-query authenticated `/v1/models` to confirm sleep-idle-seconds and sampling arguments, then observe actual idle memory release before claiming idle unload is active.

Post-application verification, 19 October 2026 at approximately22:50UTC: Ryan reported the Windows command/restart complete. Fresh authenticated router reads confirm all three presets now include `--sleep-idle-seconds 300`;27B includes `--reasoning-effort medium`; MoE includes temperature1/top_p0.95/top_k20/min_p0/presence_penalty1.5/repeat_penalty1. Context/GPU/cache/Flash Attention settings remain as previously recorded. Router remains max_instances1/autoload enabled, and all three models report unloaded. No new model was loaded to test the timer, so native configuration is verified but a complete timed idle cycle and GPU memory release remain unobserved.

### Memos — 19 October 2026

Added Memos at `http://192.168.0.18:5230` (LXC113), bringing the catalog to28. Its tile uses the existing direct TCP reachability collector; the host inventory includes.18. Application login/CRUD checks are separate from this port check. Ryan requested no login: a LAN auto-login proxy grants the shared owner account; service description is simply “Notes.” Deployment backup `/opt/homelab-dashboard/backups/20261019T230212753372Z`. Full UI smoke and the new `memos-check.mjs` pass, including448px/1440px Memos layouts and the Homepage tile/status. Deployment/recovery details live in `../homelab-memos/README.md`.

Read-only Open WebUI SSH diagnostics were also installed; see `../homelab-tools/README.md`. The A310 Homepage prompt is unchanged. A later router read at about23:04UTC observed27B in `sleeping` state; no GPU-memory measurement or full timed cycle was taken.

### Gemma replacement — 20 October 2026

Ryan's router now serves `gemma-4-26b-a4b` in place of Qwen Coder. Updated the saved/offline profile and chat link, and removed Coder's old 90 tok/s benchmark from the current UI. Gemma has no recorded benchmark yet. Live router arguments report 32,768 context, Q3_K_XL, requested GPU layers99, q8_0 caches, Flash Attention and idle sleep300s. Sampling was preserved. Deployment backup: `/opt/homelab-dashboard/backups/20261020T133708066394Z`.

`ai-check.mjs` passed against the deployed page at360/448/1920/3440px, including model links, secret exclusion, offline/cold-start/stale/recovery states. Separate Open WebUI verification confirmed Gemma's read-only Unraid tool call and final response. The old Coder profile is inactive with history preserved; all three current presets have the diagnostic tool enabled.


### Audit fixes and expanded A310 context — 20 October 2026

The initial prompt was restricted in application code to disk readings and backup activity, not by lack of model capability or access to all existing dashboard observations. `nightly/qwen_context.py` now projects allowlisted fields from Homepage telemetry/storage and, for log questions, the retained-log snapshot and fixed Glances service probe. Keyword selection bounds the source set; actual tokenizer counts prune detail and then sources until the prompt fits. Included and budget-omitted sources are reported. This is deterministic retrieval, not a model-controlled SSH agent or an exhaustive search of every file. Stale/error sources withhold values; a failed read never proves a healthy host. Counts/history describe the returned samples, not complete coverage. Secrets in log text are scrubbed on a best-effort basis; private notes and chats are not ingested.

Dated operational configuration supplies topology, the 2.5 Gbps plan, gaming-PC availability expectations, backup membership, cache guard and applied 100 GiB reserve setting. These notes are explicitly labeled recorded configuration rather than live checks; update them when the configuration changes. Live sources cover the current telemetry's monitored machines/apps only. History remains 24 hours; logs are retained warning-pattern matches, not all application logs. General knowledge has no current internet lookup and the 4B model can oversimplify technical details.

The on-demand runtime passed live 4,096-context tests: host/RAM/container answer, historical log/coverage answer, and a general RAID-versus-backup explanation. Generation measured 15.7–16.0 tokens/s, with 746–1,482 input tokens, 873–994 output tokens including reasoning, and 84–102 seconds total per cold session. Observed A310 VRAM was 2,866 of 4,048 MiB; this is a sample, not a peak guarantee. General answers remain model interpretations, not a comprehensive accuracy assessment. `nightly/verify_context_live.py` is an explicit live smoke test; it is not scheduled. Private results remain on Unraid.

Validation: 87 dashboard unit tests, 6 diagnostics tests, 2 Memos proxy tests; isolated Chrome Qwen checks at 360/448/1920/3440 px with synthetic POSTs. No frontend test triggers inference. Dashboard rollback `/opt/homelab-dashboard/backups/20261020T150135580311Z`; pre-expansion broker rollback `/mnt/disk1/.homelab-nightly/backups/20261020T145710536139Z`; latest broker backup `/mnt/disk1/.homelab-nightly/backups/20261020T151321218531Z`. Nightly timing, model, lock and resource gates remain in place.

Proxmox job `backup-e08b168e-2331` now includes LXC112, preserving its schedule/storage/retention and other members. First archive `/mnt/pve/unraid/dump/vzdump-lxc-112-2026_10_20-09_59_47.tar.zst` succeeded at 10:02 EDT. Full isolated restores passed for Memos113 and Open WebUI112: SQLite integrity, active services and local application HTTP checks. Temporary IDs9113/9112 had no external network interfaces, onboot disabled, and were removed afterward. In isolated112 only, the network-online wait was bypassed so Docker could start without an interface. Production containers remained running. See the Memos and tools READMEs for their error-handling fixes.


A live FileBrowser question initially mixed backup recovery with application recovery and described a live check as log evidence. The source selector now matches backup/RAID terms explicitly (not “recovery” or the “raid” substring in “Unraid”); current systemd observations are a separate `live_service_checks` source ahead of historical logs. Regression tests cover both distinctions. The original failed semantic check is retained privately alongside its corrected retest; HTTP completion alone is not treated as factual success.

The final same-question retest correctly separated the historical status1 failure from the independently measured `running_again` state and left root cause unknown. It used1,884 input tokens, generated1,000 tokens at15.49tok/s, and completed in89.52s. The model container was absent afterward. Private final evidence: `/mnt/disk1/.homelab-nightly/context-recovery-verification-v2-20261020.json`.


## Coverage alerts, answer evidence and cache maintenance — 20 October 2026

The recovery panel compares all non-template guests, including stopped guests, with enabled Proxmox backup jobs. Explicit IDs, all-guests jobs, exclusions and node filters are supported. Unsupported pool selectors, invalid/empty guest inventory or failed queries produce unknown coverage, not a clean result. A completed job remains separate from coverage. The live inventory is **8/13 covered**: running Kasm103 and stopped Kali100, Ubuntu101, VM109 and Windows110 are outside enabled jobs. No backup membership was changed in this follow-up. Six tests in `test_backup_coverage.py` cover these selectors and failure paths.

Each new Qwen answer has an expandable **Show evidence** view containing the exact post-pruning observations supplied to the model, capture time, dated configuration, selected sources and budget omissions. Sources not selected for the question are listed separately. Evidence is escaped text, bounded to24KiB serialized, retained only with the in-memory job and expires after15minutes. The private broker response cap is128KiB. General knowledge is not sourced by this panel; freshness describes capture time. Old jobs without evidence say so explicitly.

The full live Homepage→Qwen→evidence test correctly reported100GiB and all five backup gaps. It used2,517 input and962 output tokens, generated15.20tokens/s and completed in79.73seconds. Private result: `/var/lib/homelab-dashboard/evidence-verification-20261020.json` in104. `verify_evidence_live.py` is an explicit smoke test and is not scheduled. The93 dashboard tests pass. Qwen/evidence and coverage browser fixtures pass at360/448/1920/3440px; an actual live448px view shows matching8/13 evidence without horizontal overflow. Screenshots are local ignored `.preview/evidence-live-448.png` and `coverage-live-448.png`.

The stock array stop exceeded the initial script's30second request timeout; it continued and reached STOPPED. We verified the stopped state before applying the reserve, then used stock start, restored the prior six containers and Qwen manager/cron, and verified the effective setting. No forced unmount was used. Boot evidence and original configuration: `/boot/config/homelab-backups/reserve-20261020T153143Z/result.json`; persistent-setting backup: `/boot/config/homelab-backups/20261020T153349Z`. The result retains the historical timeout alongside statuscomplete and its recovery note. The saved maintenance script now allows300seconds and waits for Docker readiness; that revised script was not rerun end-to-end.

The second-copy audit found no verified independent Memos/OpenWebUI backup in inspected jobs and mounts. Proxmox backups and the flash-backup script target the same Unraid array. The Mac has no Time Machine destination. Unraid had no mounted external backup disk; its configured rclone remotes were zurg/torbox WebDAV mounts. Installed Unraid Connect plugins do not establish that cloud flash backup is enabled; its status was not verified. Ryan still needs to select a destination before a second encrypted copy can be configured. No data was uploaded to a new cloud service.

Deployment rollback paths: Proxmox probe `/etc/glances/homelab-backup-20261020T153645Z`; Qwen broker `/mnt/disk1/.homelab-nightly/backups/20261020T153651581840Z`; dashboard `/opt/homelab-dashboard/backups/20261020T153733932072Z`.

## Action guard — 20 October 2026

The DNS pause and log-clear actions now require the `X-Dashboard-Action: 1` header from the dashboard's own Host/Origin (`qwen_proxy.same_origin`, shared with `/api/qwen`). Cross-site, headerless or wrong-host POSTs get 403 before anything runs, and a malformed Content-Length gets 400. The Pi-hole pause timer is clamped to 1–3600 s. `/api/telemetry` no longer sends `Access-Control-Allow-Origin: *`. Applied by `patch_actions` (`# HOMELAB_ACTION_GUARD_V1`); five tests in `test_action_guard.py`. Live checks passed: four forged action POSTs returned 403, the telemetry CORS header was gone, and the real Pause button sent the header (request aborted in the browser, so DNS was never paused). Rollback: `/opt/homelab-dashboard/backups/20261020T162938155580Z`.
