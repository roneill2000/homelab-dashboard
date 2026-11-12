# Spec: search grabs go straight to the debrid services (no RdtClient)

Written 11 Nov 2026 by Claude for the local agent (pi); revised the same day for **Real-Debrid first, TorBox as the
fallback** (Ryan: Real-Debrid downloads are uncapped, TorBox caps him at 1 Gbps). The screen work for this spec is
finished and deployed; do not change `src/app.jsx` or the CSS. `SPEC-downloads-backend.md` tasks 1 to 4 are done.

**If you already built step 1 against TorBox, keep it.** That code is the fallback provider below; this revision adds
Real-Debrid in front of it and a `provider` field on the rows. Rename nothing that already works.

## What changes for Ryan

Today a search result's **Get** on the Debrid tab goes Prowlarr → RdtClient (set to Real-Debrid) → the `prowlarr`
folder. Nothing files it under Movies or Shows, nothing asks Jellyfin to scan, and a refused grab shows as a nameless
red row.

After this spec, **Get** goes dashboard → **Real-Debrid**, or **TorBox** when Real-Debrid refuses or fails it → the same
download worker pasted links use. Every file is then filed, scanned, shown with live progress and can be retried.

Out of scope, leave untouched: the Usenet tab (SABnzbd), Sonarr (it keeps using RdtClient), RdtClient itself, and the
`recent()` list of RdtClient grabs.

## Keys

- Two private files on LXC 104, each `{"key": "..."}`, mode `0600`, read with `_config()`:
  `/etc/homelab-dashboard/realdebrid.json` and `/etc/homelab-dashboard/torbox.json`. **Ryan or Claude puts the keys
  there. Never read RdtClient's settings for a key, never print, log, commit or send a key to the browser.** Do not
  use `/etc/homelab-dashboard/debrid.json`: that name is an old state-file location.
- The providers in use are the ones whose key file exists, always in the order Real-Debrid, then TorBox. With neither,
  everything here is off: `search` is not in `features` and Get works as today (Prowlarr → RdtClient).
- For read-only checks while building, a key in a 0600 file in your own container is fine (e.g. `/root/.torbox_key`).
  Delete those files when the feature is deployed.
- One helper per provider with a 20 s timeout that raises `RuntimeError(<the service's own reason>)`; error text
  shown to Ryan is that reason, cut to 200 characters, with any key scrubbed out.

## Real-Debrid API (primary)

**Confirmed 11 Nov by Claude from the official docs page** `https://api.real-debrid.com/` (plain HTML; `curl` it).
Base `https://api.real-debrid.com/rest/1.0`, header `Authorization: Bearer <key>`. Limit **250 requests a minute**
(a 429 counts too and repeated ones can block the account). Errors: HTTP status plus JSON `{"error": "<text>",
"error_code": <int>}`.

| Purpose | Call |
|---|---|
| Add a magnet | `POST /torrents/addMagnet`, form `magnet=<link>`. 201 → `{"id", "uri"}`. |
| Add a .torrent | `PUT /torrents/addTorrent`, the raw file bytes as the body. 201 → `{"id", "uri"}`. |
| Read one torrent | `GET /torrents/info/{id}` → `id`, `filename`, `original_filename`, `hash`, `bytes` (selected files), `original_bytes`, `progress` (**0 to 100**), `status`, `files[]` (`id`, `path` starting with `/`, `bytes`, `selected` 0/1), `links[]` (hoster links, one per selected file), `speed` and `seeders` only in some states. |
| Statuses | `magnet_error`, `magnet_conversion`, `waiting_files_selection`, `queued`, `downloading`, `downloaded`, `error`, `virus`, `compressing`, `uploading`, `dead`. |
| Choose files (**required** before it starts) | `POST /torrents/selectFiles/{id}`, form `files=<comma-separated ids>` (or `all`). 204; 202 = already done. |
| Turn a hoster link into a download | `POST /unrestrict/link`, form `link=<one entry of links[]>` → JSON with `download` (the https file link), `filename`, `filesize`. |
| Remove a torrent | `DELETE /torrents/delete/{id}`. 204. |

Not usable: `GET /torrents/instantAvailability/{hash}` (error_code 37, "Disabled endpoint"). Error codes that mean
**"Real-Debrid will not do this torrent"** (go to TorBox): 28 File not allowed, 29 Torrent too big, 30 Torrent file
invalid, 35 Infringing file, 36 Fair Usage Limit, 23 Traffic exhausted, 21 Too many active downloads. A bad key (8, or
HTTP 401) or account trouble (9, 14) is not a torrent problem: say so to Ryan and use TorBox for this grab.

**`links[]` order.** Real-Debrid lists one hoster link per selected file, in the order of the selected files' `id`s.
Do not rely on that alone: match each `unrestrict/link` answer to a file by `filename` and `filesize`, and if a torrent
was packed into one archive (one link for several files, the answer's `filename` ends in `.rar` or `.zip`), fail that
grab at Real-Debrid and fall back to TorBox.

## TorBox API (fallback)

**Requests confirmed 11 Nov by Claude** from `https://api.torbox.app/openapi.json` (no key needed);
**torrent and file fields confirmed with a live read-only `mylist`.** Base `https://api.torbox.app/v1/api`, header
`Authorization: Bearer <key>`. A 404 `{"detail":"Not Found"}` means a wrong path; 401 a missing key.

**User-Agent matters (confirmed 12 Nov 2026 by pi with a key):** their Cloudflare rejects urllib's own
`Python-urllib/*` with `403` and body `error code: 1010`, and answers `200` for the same URL with any other UA
(`homelab-dashboard` works, `curl/8.5.0` works). Real-Debrid does not care: `200` with the default UA. So every
TorBox call from the dashboard sends a `User-Agent`; a TorBox feature that only ever got 403 was never live.

| Purpose | Call |
|---|---|
| Add a torrent | `POST /torrents/createtorrent`, multipart form `magnet` or `file` (the .torrent bytes), optional `name`, `seed`, `as_queued`, `add_only_if_cached`. |
| Read one torrent | `GET /torrents/mylist?id=<id>&bypass_cache=true`. Envelope `{"success", "error", "detail", "data"}`. Torrent: `id`, `hash`, `name`, `size`, `progress` (**0 to 1**), `download_state`, `download_finished`, `download_present`, `cached`, `seeds`, `download_speed`, `eta`, `files[]` (`id`, `name`, `short_name`, `size`, `mimetype`, `infected`). Ready = `download_finished and download_present`. |
| Link for one file | `GET /torrents/requestdl?token=<key>&torrent_id=<id>&file_id=<id>` (the key goes in `token`). `data` = the https link. Throttled (about 20 a minute reported). **Never `redirect=true`** (a permanent link with the key in it). |
| Remove a torrent | `POST /torrents/controltorrent`, JSON `{"torrent_id": <id>, "operation": "delete"}`. Never send `all`. |
| Is a torrent already at TorBox (cached check, Task 3) | `GET /torrents/checkcached?format=object&hash=<a>,<b>,<c>` (repeated `hash=` works too; `format=list` returns an array). Confirmed live 12 Nov 2026 with a key: envelope `{"success", "error", "detail", "data"}`, and `data` is `{"<hash>": {"name", "size", "hash"}}` **only for the hashes that are cached** - an uncached hash is simply absent, there is no `false`. About 100 hashes per call (their docs), one call is well under 1 s, and TorBox caches its own answer for an hour, so ask once per search and never in a loop. `list_files=true` adds the file list (not used). |

TorBox downloads are capped at about 1 Gbps on Ryan's plan; that is why it is second.

## Flow

### 1. Get (`POST /api/action/download` with `{"result": "<token>"}`, torrent results only)

- `search()` stores `magnetUrl`, `downloadUrl` and `infoHash` from Prowlarr's row in `RESULTS[token]` (server only;
  `downloadUrl` contains Prowlarr's API key).
- Get the torrent: `magnetUrl` when it starts with `magnet:` (check with `MAGNET`); otherwise fetch `downloadUrl`
  (a `192.168.0.13:9696` address; 15 s, at most 5 MB, no automatic redirects). A redirect to `magnet:` is that magnet;
  a bencoded body containing `4:info` is a .torrent. Anything else: 503 `The indexer did not give a usable torrent
  for this result.`
- **Try Real-Debrid first** (`addMagnet` or `addTorrent`). A "will not do this torrent" error, or account trouble, goes
  straight to **TorBox** (`createtorrent`). Both refuse: 503 with both reasons, e.g.
  `Real-Debrid: infringing file. TorBox: <its reason>.`
- One row in the state file, under `_STATE_LOCK`:

```json
{"name": "rd-<id>" or "torbox-<id>", "state": "preparing", "provider": "realdebrid" or "torbox",
 "title": "<cleaned title>", "kind": null, "debrid": {"id": "<provider's torrent id>", "hash": "..."},
 "release": "<raw release name>", "started": 1794400000, "tried": ["realdebrid"],
 "prep": {"progress": 0, "seeds": null, "files": null, "provider": "Real-Debrid"},
 "dest": null, "bytes": 0, "scanned": false, "scan_error": null, "error": null}
```

  (If step 1 already stored a `torbox` object, keep reading it for old rows.) `prep.progress` is always 0 to 1 (divide
  Real-Debrid's percent by 100). `prep.provider` is shown to Ryan, so use the display names.
- Answer `{"ok": true, "title": "<title>", "via": "debrid"}`. A second Get for a hash that already has a `preparing`
  row answers ok without adding it again.

### 2. At the debrid service (in `_debrid_tick()`)

- For each `preparing` row, read its torrent (at most every 10 s per row, at most 6 rows per tick) and copy
  progress, seeders and the media file count into `prep`.
- **Real-Debrid `waiting_files_selection`:** pick the media files (rules in step 3) and `selectFiles` with their ids.
  No media file: fail at Real-Debrid (fallback below).
- **Fallback to TorBox:** when a Real-Debrid row reaches `magnet_error`, `error`, `virus` or `dead`, or has made no
  progress for 6 hours, delete it at Real-Debrid, add the same torrent at TorBox, set `provider: "torbox"`, append to
  `tried`, keep the same row (new `debrid.id`, `name` stays). Keep the magnet or .torrent bytes for this in a private
  sidecar file next to the state file (`0600`), never in the panel's rows. A TorBox row that fails has nowhere else to
  go: `failed` with both reasons.
- 24 hours without finishing at either service: `failed`, `the debrid services did not finish this torrent in 24 hours`.
- `_debrid_rows()` passes `name`, `state`, `title`, `prep` and `error`, and never `debrid`, `torbox`, `release`,
  `tried`, magnets, .torrent bytes or links.

### 3. Handing files to the worker

When the torrent is ready (Real-Debrid `downloaded`; TorBox finished and present):

- **Pick the files:** names ending in one of `MEDIA_EXT`; drop anything with `sample` in the name and anything under
  50 MB when a larger media file exists; drop TorBox files with `infected: true`. Nothing left: fail at this provider.
  More than 60: the first 60 by name, said in `error`.
- **Get each file's link:** Real-Debrid `unrestrict/link` for each `links[]` entry (at least 1 s apart: 250 a minute);
  TorBox `requestdl` (at least 3 s apart). Then plan and start exactly like a pasted link (`plan_debrid` logic with the
  file's name; for bare episode names like `S02E03.mkv`, title and kind come from the torrent's release name).
- Add a normal download row per file, with `provider` and `debrid: {"id": ..., "file": <file id>, "link": <the
  Real-Debrid hoster link, server only>}` and **no stored `url`** (download links expire).
- At most 2 files handed over per tick; the `preparing` row stays (with `prep.files` = files left) until the last one,
  then goes.
- Leave the torrent in the account when done (`_DEBRID_DELETE_WHEN_DONE = False`).

### 4. Retry for these rows

`can_retry` is true in the usual three states when the row has `debrid`. Retry asks the same provider for a fresh
link (Real-Debrid: `unrestrict/link` with the stored hoster link; TorBox: `requestdl`) and starts the worker with it.

### 5. Cancel (`POST /api/debrid` with `{"cancel": "<name>"}`)

Only for a `preparing` row; otherwise 400. Delete the torrent at its current provider, remove the row and its sidecar
file. Files already handed over keep their own rows and Stop. `clear_debrid()` keeps `preparing` rows.

### 6. Turn it on

Add `"search"` to `features` only when at least one key file is present and the whole flow is deployed, in the same
commit that sends torrent Gets down the new path.

## Tests to add (fakes only: no real Real-Debrid, TorBox, Prowlarr or worker calls)

- No key files: Get uses Prowlarr's grab as today; no `search` in `features`.
- Both keys: Get calls Real-Debrid only; on error_code 35 it calls TorBox and the row says `provider: "torbox"`; both
  refusing is a 503 naming both. Only the TorBox key: Get goes straight to TorBox.
- `waiting_files_selection` selects only the media ids (sample skipped). A `dead` Real-Debrid torrent moves to TorBox
  once; a failing TorBox row is `failed` with both reasons.
- Progress from Real-Debrid percent becomes 0 to 1 in `prep`.
- Ready: a single-file torrent becomes one row in the right folder; a 10-episode pack hands over 2 per tick under one
  show folder with the right seasons and episodes; an archive answer from `unrestrict/link` falls back to TorBox.
- Spacing: Real-Debrid calls at least 1 s apart, TorBox `requestdl` at least 3 s (inject the clock); no `redirect`.
- No key, magnet, .torrent byte, Prowlarr `downloadUrl`, hoster link, `debrid`, `tried` or `release` in any HTTP
  answer. The sidecar file is `0600`.
- Retry asks the row's provider for a fresh link. Cancel deletes at the current provider; Cancel of a non-preparing
  row is 400. Clear keeps `preparing` rows.

## Rules

- **Tools:** the web tools are `mcp__webfetch__webfetch` and `mcp__searxng__web_search` (plain `webfetch` does not
  exist). For API docs, `bash` with `curl` is simplest.
- Pull and rebase first, small commits (no AI credit), `npm run build`, `npm run check` (`ALL CHECKS OK`) and
  `npm test` before each, deploy only when Ryan asks, one line per deploy in `/mnt/share/hermes-changes.md`.
- Use the 262k Coder or the 100k 27B Long for this work; the 64k 27B compacts too often for a two-file change.
- **One real test at the end, started only by Ryan:** Get on one small, well-seeded result. Do not grab anything.
- If a service's real behaviour contradicts a step here, stop and tell Ryan what you found.

## How to tell it worked

| Step | What Ryan sees on Media & downloads |
|---|---|
| Get on a Debrid-tab result | Toast "Sent to debrid. It shows under Downloads." and an "At debrid" row with progress, seeders, files and Cancel. |
| Real-Debrid refuses | The same row carries on at TorBox (its reason is in the row's details). |
| The service finishes | One normal download row per file, filed under Movies or its show folder, with live progress. |
| A download finishes | "Adding to library", then "In library". |
| Both refuse | An error at once naming both services' reasons. No nameless red row. |

## Status

Committed on `main`: step 1 (Get puts the torrent in the account and shows the watching row, Real-Debrid first, TorBox
fallback, rate-limit refusals separated from torrent refusals), step 2 (the tick watches a watching row at its own
service: progress, seeders, file count, files chosen at Real-Debrid before it starts, 24 h move to the fallback,
stalled and dead rows named, the magnet or .torrent bytes kept in a 0600 sidecar file next to the state), step 3
(a ready torrent hands up to 2 of its files per tick to the download worker: media files chosen by the usual rules,
each file's link resolved at the service just before aria2c is told it, the plan and the start call the same ones a
pasted link uses, the row keeping the provider's link and never a url, an archive answer moving the torrent to TorBox,
calls kept 1 s and 3 s apart). `search` is still off in `features`; the browser screen waits for that.

Left: step 4 (Retry asks the row's provider for a fresh link), step 5 (Cancel on a watching row deletes at the
provider and frees the row), step 6 (turn `search` on), then Ryan's one real Get.
