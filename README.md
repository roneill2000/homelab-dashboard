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

Recovery uses bounded Proxmox backup task queries (20 archived and 20 active, cached 60s). It distinguishes latest job result from last successful job; it does not verify guest coverage, backup contents or restores. Unraid live `var.ini` supplies operation state and completion timestamps; assigned devices come from `disks.ini`, excluding empty slots. Completed means idle, exit 0 and a valid finish timestamp at/after start. An active/paused operation never displays a completion result. Source failures become unknown, never healthy.

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

Ryan approved a 100 GiB cache pool reserve, but then chose to keep services running. Unraid 7.3.2 disables the pool reserve field while the array is started. **The live reserve remains 30,000,000 KiB (~28.6 GiB); 100 GiB is pending a maintenance window.** `python3 apply_cache_reserve.py` is read-only. `--apply` refuses unless the array is already stopped, uses Unraid's own `emcmd` setting handler, and verifies persistent configuration. It never stops the array itself. Do not apply until Ryan authorizes that interruption.

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

Overview now has a single-question prompt bar for the same local Qwen3.5-4B model. It includes a small snapshot of current disk readings and backup activity, with source freshness and units; parity capacity is explicitly not applicable. It has no live log search, conversation history, browsing or execution tools. Answers show when readings were captured and remain advisory. The latest question/answer is held only in broker memory and expires after 15 minutes; browser input stays in that tab.

`nightly/qwen_service.py` is a lightweight authenticated broker at Unraid `192.168.0.4:11438`. Only the dashboard and local host addresses are accepted. `qwen_proxy.py` keeps its token in a root-only file on LXC104; the browser never receives it. Submission requires a same-origin request with a custom header. The existing dashboard remains a private LAN application with no user login. Model inference itself still binds only to Unraid localhost.

The broker and nightly runner share `run.lock` for the whole GPU session. A busy nightly job rejects prompts; the scheduled job waits up to 20 minutes if a question is running. Each question checks fresh backup-idle state and ≥1 GiB cache free before loading and again before generating. It refreshes measurements after loading, uses the model's tokenizer to cap input at 850 tokens within the 2,048-token context, and caps generation at 1,152 tokens including reasoning. Questions are limited to 1,000 characters and can still require shortening to fit the token budget. No partial/reasoning output is published.

`python3 nightly/install_qwen.py` installs the broker and shared runner with private backups and remote credentials. Installation takes the GPU lock and refuses while a model job is active. Persistent `/boot/config/plugins/dynamix/homelab-qwen.cron` checks the singleton broker every minute; this starts only its lightweight manager, not the model. The container starts on a submitted question and is stopped and verified before the answer becomes complete. `stop_model()` attempts forced removal of only the dedicated model container if normal shutdown cannot be verified. The nightly report also publishes success only after verified cleanup.

Validation: 65 offline Python tests, `node qwen-check.mjs` (intercepted requests, timestamp/expiry/error/security/layout fixtures), existing UI smoke and live nightly checks. A real question submitted through the phone-sized homepage correctly returned array usage 66.4% and free space 1,345.5 GB from its captured readings. Its model container was removed afterward and all six existing Docker apps remained running. Final deployed runner/broker/scrubber hashes matched local sources. The minor parity-label and pre-success cleanup refinements were covered offline after that real question; no extra expensive inference was needed.

Final prompt deployment backup: `/opt/homelab-dashboard/backups/20261019T200332573559Z`; broker/runner backup: `/mnt/disk1/.homelab-nightly/backups/20261019T200325209723Z`. Snapshot redaction deployment backup: `/usr/local/bin/homelab-syslog-backup-20261019T200109Z`. That monitor restart reset its in-memory alert ring; retained files were preserved. See `AUDIT-QWEN-2026-10-19.md` for the Sol review and remaining limits.

To disable on-demand prompts, remove only `/boot/config/plugins/dynamix/homelab-qwen.cron`, rebuild cron with `/bin/bash /usr/local/sbin/update_cron`, and terminate only the idle `qwen_service.py` manager. Do not kill a running question or remove the shared nightly files. This does not disable the nightly review. Restore the dashboard backup manifest if removing the prompt UI/proxy as well; keep credentials private.
