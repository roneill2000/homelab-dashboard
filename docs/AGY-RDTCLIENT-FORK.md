# agy task: RdtClient fork with Real-Debrid and TorBox at the same time

## Goal

One RdtClient that can use **both** Real-Debrid and TorBox. Each torrent remembers which service it is on. New torrents try the **primary** service first (Real-Debrid). If the primary doesn't start the download within a time limit, RdtClient removes it there and adds it to the **fallback** service (TorBox). Sonarr, Readarr, Prowlarr and the dashboard keep using the same qBittorrent-style API on port 6500, with no changes on their side.

Why: RdtClient has one global provider setting. Ryan added a magnet that he knows is cached on Real-Debrid. RdtClient is set to TorBox, so it sat at `metaDL` with 0 seeders and never started.

## Current setup (verified 28 Oct 2026)

- RdtClient runs as `rdtc.service` in **LXC 102** (`utilities`, 192.168.0.13), port **6500**. Proxmox host: `root@192.168.0.3`. Use `pct exec 102 -- …`.
- Upstream: https://github.com/rogerfar/rdt-client. The last commit checked was `a669bd3` (18 Oct 2026). Install the same version that runs now (`/Api/Version` or the UI footer), not upstream `main`, unless they match.
- Setting `Provider:Provider = 3` (TorBox). Upstream enum: 0 RealDebrid, 1 AllDebrid, 2 Premiumize, 3 TorBox, 4 DebridLink. Check this in the source, don't assume it.
- Categories in use: `prowlarr` (manual grabs and dashboard magnets), `readarr`, `tv`.
- Clients of the qBittorrent API, all on `127.0.0.1:6500` or `192.168.0.13:6500`:
  - Sonarr (category `tv`)
  - Readarr (`readarr`)
  - Prowlarr (`prowlarr`)
  - the homelab dashboard (`~/homelab/download_proxy.py` and `activity_monitor.py`), which calls:
    - `/Api/Authentication/Login`
    - `/api/v2/transfer/info`
    - `/api/v2/torrents/info`
    - `/api/v2/torrents/add` (form fields `urls`, `category`)
    - `/Api/Torrents`
- Stuck example: the newest `prowlarr`-category item (added 28 Oct 21:24 UTC), `rdStatusRaw: metaDL`, `rdSeeders: 0`, category `prowlarr`. Ryan says it is cached on Real-Debrid. Use it as the fallback test **only with Ryan's OK**.

## Rules

1. **Secrets:** never print, log, commit or screenshot the Real-Debrid or TorBox API keys, RdtClient's password, or Sonarr's or Prowlarr's API keys. Ryan pastes the RD key into the RdtClient settings page himself.
2. **Where the code lives:** clone the fork to `/mnt/share/repos/rdt-client` (the n100 share, next to `homelab-android`). Push only to n100. Ryan pushes to GitHub himself. No AI credit or `Co-Authored-By` lines in commits.
3. **Where to build:** only on the Mac or on the Proxmox host. Never on LXC 102. Copy the built output over.
4. **Keep the diff small and isolated**, so rebasing on upstream stays easy. Put new logic in new files where possible, and keep edits to upstream files short. Keep a list of upstream files you touched in `FORK.md` at the repo root.
5. **No real downloads without Ryan's OK.** Ask before adding any torrent, and name the exact magnet or test file.
6. Don't change Sonarr, Readarr or Prowlarr settings.
7. Back up before replacing anything on LXC 102 (see Deploy), and be able to roll back in under 2 minutes.

## Steps

### 1. Map the code (read only)

- Find every place the global provider is read. Search for `Provider.Provider`, the provider enum, and the factory or switch that returns the debrid client (`ITorrentClient` or similar).
- Find the torrent entity (EF Core), its migrations, the background runner that polls debrid status, the qBittorrent emulation (`QBittorrent.cs` and its controller), and the settings model and UI.
- Write the map into `FORK.md` before changing anything.

### 2. Per-torrent provider

- Add a nullable `Provider` column to the torrent entity, with an EF migration. `null` means "use the global primary", so existing rows keep working unchanged.
- Replace the global client lookup with `GetClient(torrent.Provider ?? primary)` wherever a call acts on an existing torrent: status polling, file selection, unrestrict, delete, retry.
- Settings additions:
  - `Provider:Secondary` (enum, default none)
  - `Provider:SecondaryApiKey`
  - `Provider:FallbackEnabled` (bool)
  - `Provider:FallbackSeconds` (default 60)
  - Show these on the existing provider settings page. Mask the key the same way the primary key is masked.
- Ryan's target config: primary **RealDebrid**, secondary **TorBox**, fallback on.

### 3. Choosing a provider when a torrent is added

- Default: primary.
- Optional per-category override, a setting such as `Provider:CategoryMap` (`tv=TorBox`, …), so Sonarr can be pinned if Ryan wants.
- The qBittorrent `torrents/add` endpoint also accepts an optional form field `provider` (`realdebrid` | `torbox`). Unknown values are rejected with 400. Sonarr never sends it, so its behaviour is unchanged.

### 4. Fallback

- In the background runner, if fallback is enabled and a torrent on its first provider still hasn't reached a downloading or downloaded state after `FallbackSeconds`, fall back. "Not started" means: still waiting on metadata or queued with 0 seeders, or the provider returned a not-cached or error state. Take the exact status values from the provider clients.
- To fall back:
  1. Delete the torrent at the first provider.
  2. Add the same magnet or torrent file at the secondary.
  3. Set `Provider` and keep the same hash and category.
  4. Log one line, for example: `fallback RealDebrid→TorBox after 60s (metaDL, 0 seeders)`.
- Fall back once at most. If the secondary also fails, use upstream's existing error and retry handling.
- If TorBox's cached-check endpoint is available, it may be used to skip waiting when TorBox is primary. Real-Debrid's instant-availability endpoint was disabled in late 2024: confirm that, and don't depend on it.

### 5. Show the provider

- `/Api/Torrents` includes `provider`.
- qBittorrent `torrents/info` puts the provider in `tags` (for example `rd` or `torbox`). Sonarr ignores tags, and the dashboard will read them.
- The RdtClient web UI shows the provider on each torrent row.

### 6. Tests

- Unit tests (in the existing test project, or a new one):
  - provider resolution: `null` → primary, category map, form field
  - the fallback state machine: it times out, falls back once, and never twice
  - the migration on a copy of the real database: copy `rdtclient.db` from LXC 102 to the build machine, migrate the copy, and check existing rows read as `Provider=null`
- `dotnet build` and `dotnet test` pass. The frontend builds if you touched it.

### 7. Deploy to LXC 102

1. Find how RdtClient is installed: the unit file, binary directory, data and DB path, and which user it runs as. Record it in `FORK.md`.
2. Back up first:
   - stop `rdtc`
   - copy the install directory and the DB to `/root/rdtc-backup-<timestamp>/`
3. Copy the build over, then start `rdtc`.
4. Checks, all **GET only**:
   - `/api/v2/app/version` works
   - `/api/v2/torrents/info` lists the same items as before
   - Sonarr's and Readarr's Activity pages show no download-client errors (check their health endpoints)
   - the dashboard's Media page still shows the recent list
5. Ask Ryan to paste his RD key into Settings → Provider and set primary RD, secondary TorBox, fallback on.
6. With Ryan's OK, retest the stuck magnet above. Expected: added on RD, done within seconds because it's cached.
7. Rollback: stop, restore the backup directory and DB, start.

### 8. Stop updates from overwriting it

- Check whether the homelab update runner (`~/homelab/updates/`, `homelab-update` on Proxmox) or any other job updates RdtClient on LXC 102. If one does, exclude `rdtc` from it and add a note to `FORK.md`.
- Add `scripts/sync-upstream.sh` to the fork:
  1. fetch `upstream`
  2. rebase the fork branch onto the latest upstream release tag
  3. run the tests
  4. print the result

  It never deploys on its own.

## Done when

- One `rdtc` serves Sonarr, Readarr, Prowlarr and the dashboard on port 6500, with the RD key and TorBox key both set.
- A cached-on-RD magnet completes through RD. A magnet RD can't start falls back to TorBox after 60 s, and the log line shows it.
- Existing torrents and Sonarr/Readarr behaviour are unchanged.
- `FORK.md` lists: the touched upstream files, the install layout, the backup and rollback commands, and the sync steps.
- Commits are on n100 for Ryan to push.

## Report back to Ryan

- what changed, as a list of upstream files touched and new files
- the test output
- the deploy checks, each with its result
- anything skipped or left for him

The dashboard side (provider labels, real status text, a remove button for stuck items) is done separately in `~/homelab` once the fork's `tags` and `provider` fields are live.
