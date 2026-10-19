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
