# Homelab dashboard

Custom React dashboard for http://192.168.0.2/. Dark control room with service search, browser-local favorites, responsive navigation, live host telemetry, and disk history. Built 19 October 2026.

## Build and verify

```sh
npm ci
npm run build
python3 -m unittest -v test_storage.py
npx playwright install chromium
node ui-smoke.mjs
# Or use already installed Chrome in an isolated temporary profile:
PW_CHANNEL=chrome node ui-smoke.mjs
```

`ui-smoke.mjs` uses an isolated Playwright browser against the live dashboard. All action POST requests are intercepted and aborted; DNS and log settings are never changed by the test. Screenshots are local in ignored `.preview/`.

`python3 preview.py` serves a localhost-only preview on port 8799. It proxies existing telemetry, maintains its own ignored disk-history database, and refuses POST actions.

## Deployment

Homepage is Proxmox LXC 104. Existing Python service `homelab-dashboard` listens on port 80. Its secret-bearing integrations remain on the server and are never pulled into this project.

`python3 deploy.py` uploads the built HTML and storage collector through SSH to Proxmox. It patches only the HTML assignment, storage import, and GET route in the existing backend. It checks syntax, creates a private timestamped backup, restarts only the dashboard service, checks its API, and restores the previous files if its health check fails.

Remote files:
- `/usr/local/bin/homelab-dashboard.py`: existing service, minimally patched.
- `/usr/local/bin/storage_monitor.py`: read-only collector.
- `/opt/homelab-dashboard/index.html`: compiled React page; React runtime remains locally hosted in `/static/`.
- `/var/lib/homelab-dashboard/metrics.sqlite3`: rolling 24-hour SQLite history, survives service restarts.
- `/opt/homelab-dashboard/backups/20261019T172419592725Z`: original deployment rollback copy. Keep it private: the backend includes existing credentials.

To restore the original page, inside LXC 104 copy `homelab-dashboard.py` from that backup to `/usr/local/bin/homelab-dashboard.py`, then `systemctl restart homelab-dashboard`. Keep the history database. For later backups, restore the files listed as existing in that backup's `manifest.json` as well.

## Storage definitions

Both hosts already expose Glances v4 at port 61208. No new host agent or API credential is needed. The collector reads `diskio` and `fs`, subtracts cumulative byte counters, and stores real samples; absent/reset counters are null, while verified unchanged counters produce zero. Sampling waits 10 seconds after each collection, so actual intervals include network time. Larger windows average readings to approximately 240 points; charts preserve gaps.

| Card | Host | Device | Filesystem |
| --- | --- | --- | --- |
| Proxmox scratch | .3 | sda | `/mnt/share`, SMB `n100` |
| Unraid array | .4 | sdc | `/mnt/disk1` (Glances `/rootfs/mnt/disk1`) |
| Unraid cache | .4 | sdd | `/mnt/cache` (Glances `/rootfs/mnt/cache`) |
| Unraid parity | .4 | sdb | No filesystem capacity |

Mappings were checked against Proxmox mounts and Unraid's `/var/local/emhttp/disks.ini`. Recheck after replacing or adding disks. The current Unraid array has one data disk; this is not automatic multi-disk discovery. Parity throughput is separate and never added to data throughput. Speeds use decimal MB/s; capacity uses decimal GB/TB. These are physical disk rates, not SMB network transfer rates. Busy percentage is unavailable in these Glances responses. I/O latency is shown when cumulative timings and operation counts are available and work occurred.

History began at deployment, 19 October 2026 at 13:24 EDT. There is no historical backfill. SABnzbd's separate speed chart records the current browser session only. Existing host and application telemetry collectors remain unchanged. Services without direct monitoring are labeled unmonitored.

## Validation

Three Python tests cover cumulative rates, counter resets, missing-vs-idle readings, persistent history queries, and parity separation. Live API checks passed for all four windows and invalid input. A dashboard-service restart preserved recorded history. UI smoke checks are recorded in the session handoff.

`baseline.html` preserves the original public HTML with initial live telemetry stripped. `src/` is the editable frontend; `index.html` is generated.

## Personal layout revision

Ryan requested removal of decorative copy and better phone/large-screen use. Page subtitles, taglines, and onboarding copy are removed. Metric definitions are collapsed under “Metric details”. Phone controls use 44px targets; charts have larger axis labels; the fleet table becomes cards. Larger screens use a wider content area, four-column storage/service grids and drive-health cards.

`DASHBOARD_URL=http://192.168.0.2/ node responsive-check.mjs` checks Overview, Storage, Services and Fleet at 360×800, 412×915, 448×998, 998×448, 1440×1000, 1920×1080, 2560×1440 and 3440×1440. It checks both page and card overflow, mobile topbar target sizes and JavaScript errors, and captures phone/desktop/ultrawide screenshots. The mobile contexts use touch and DPR 3. The 448×998 case approximates the Pixel 11 Pro XL panel's 1344×2992 aspect ratio, per [Google's specifications](https://store.google.com/us/product/pixel_11_pro_specs?hl=en-US); Android display-size settings and browser chrome can change the actual CSS viewport. This is browser emulation, not an on-device test.
