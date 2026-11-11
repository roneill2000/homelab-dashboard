# Spec: search grabs go straight to the debrid service (no RdtClient)

Written 11 Nov 2026 by Claude for the local agent (pi). Ryan asked for this. **Do `SPEC-downloads-backend.md` first**
(this spec builds on its Retry and Dismiss work). The screen work for this spec is finished and deployed; do not change
`src/app.jsx` or the CSS.

## What changes for Ryan

Today a search result's **Get** on the Debrid tab goes Prowlarr → RdtClient → debrid service → the `prowlarr` folder.
Nothing files it under Movies or Shows, nothing asks Jellyfin to scan, and a refused grab shows as a nameless red row.

After this spec, **Get** goes dashboard → TorBox (the debrid service) → the same download worker pasted links use. Every
file is then filed, scanned, shown with progress and can be retried, exactly like a pasted link.

Out of scope, leave untouched: the Usenet tab (SABnzbd), Sonarr (it keeps using RdtClient), RdtClient itself, and the
`recent()` list of RdtClient grabs (old grabs still show until they age out).

## Before you write any code: confirm the TorBox API

**Requests: confirmed 11 Nov by Claude** from TorBox's own API description, `https://api.torbox.app/openapi.json`
(fetch it with `curl`; no key needed. `https://api-docs.torbox.app/` is a script-rendered page and returns almost
nothing to a fetch). **Answers: not described there.** Read the real field names with one read-only call before coding:
`curl -s -H "Authorization: Bearer $KEY" "https://api.torbox.app/v1/api/torrents/mylist?limit=1&bypass_cache=true"`.
If a step below needs a field that does not exist, stop and tell Ryan.

Base URL `https://api.torbox.app/v1/api` (the root answers `{"success":true,"detail":"API is running."}`; an unknown
path answers `{"detail":"Not Found"}` with HTTP 404, so a 404 means a wrong path, not a bad key; a missing key is 401).
Auth header `Authorization: Bearer <key>`. Your own account is `GET /user/me` (not `/me`).

| Purpose | Call | Status |
|---|---|---|
| Add a torrent | `POST /torrents/createtorrent`, **multipart form**: `magnet` or `file` (the .torrent bytes), optional `name`, `seed`, `allow_zip`, `as_queued`, `add_only_if_cached`. | Request confirmed. Answer fields (`torrent_id`, `hash`, `name` per a changelog): verify. |
| Read one torrent | `GET /torrents/mylist?id=<torrent_id>&bypass_cache=true` (also `offset`, `limit`, `files`, `active`). | Request confirmed. The list is otherwise cached about 10 minutes. |
| Torrent fields | Expected: `id`, `name`, `size`, `progress` (0 to 1), `download_state`, `download_finished`, `download_present`, `seeds`, `download_speed`, `files[]` with `id`, `name`, `short_name`, `size`. | **Not described. Verify every name and the `download_state` values with the call above.** |
| Link for one file | `GET /torrents/requestdl?token=<key>&torrent_id=<id>&file_id=<id>` (also `zip_link`, `user_ip`, `redirect`, `append_name`). The key goes in `token`, not the header. | Request confirmed. Answer `data` is expected to be the https link: verify. Links expire (1 to 3 hours reported); calls are throttled (about 20 a minute reported). |
| Many files at once | `GET /torrents/requestdl/batch?token=<key>&torrent_id=<id>&file_ids=<ids>`. | Exists. Optional: it could replace per-file calls for a season pack, but keep the 3 s spacing rule per call. |
| Is it already cached | `GET /torrents/checkcached?hash=<hash>&format=object&list_files=true`. | Request confirmed. Optional for this spec. |
| Remove a torrent | `POST /torrents/controltorrent`, **JSON** `{"torrent_id": <id>, "operation": "delete"}` (`all` also exists: never send it). | Request confirmed. |

**Never use `requestdl` with `redirect=true`.** That form is a permanent link with the API key in it, and the link is
passed to the worker on a command line and written to its log.

## The key

**For the read-only checks above** a key in a 0600 file in your own container is fine (for example `/root/.torbox_key`),
read into a variable, never printed. Delete that file when the feature is deployed.

- TorBox's API key goes in a new private file on LXC 104: `/etc/homelab-dashboard/torbox.json`, `{"key": "..."}`,
  mode `0600`, read with `_config()` like the other files there. **Ryan or Claude puts the key there. Do not look for
  it in RdtClient's settings, and never print it, log it, commit it or send it to the browser.**
- When the file is missing or has no key, everything in this spec is off: `search` is not in `features`, and Get
  behaves exactly as it does today (Prowlarr → RdtClient).
- Add a helper `torbox(method, path, ...)` with a 20 s timeout that raises `RuntimeError(detail)` on
  `success: false` or a bad HTTP status. Error text shown to the user is TorBox's `detail`, cut to 200 characters,
  with the key scrubbed if it ever appears in it.

## Flow

### 1. Get (`POST /api/action/download` with `{"result": "<token>"}`, torrent results only)

- `search()` must remember more per result: store `magnetUrl`, `downloadUrl` and `infoHash` from Prowlarr's row in
  `RESULTS[token]`, next to `guid` and `indexerId`. These stay on the server. `downloadUrl` contains Prowlarr's API
  key, so it must never be in an answer.
- On Get, find the torrent: use `magnetUrl` when it starts with `magnet:` (validate with the existing `MAGNET`
  pattern). Otherwise fetch `downloadUrl` from Prowlarr (it is a `192.168.0.13:9696` address; 15 s timeout, at most
  5 MB, do not follow redirects automatically). A redirect to a `magnet:` link is that magnet. A body starting with
  `d8:announce` or `d` and containing `4:info` is a `.torrent` file to upload. Anything else: `503` with
  `The indexer did not give a usable torrent for this result.`
- Call `createtorrent`. On failure answer `503` with TorBox's reason (for example a refused file). No fallback to
  RdtClient: the user sees the real reason at once.
- On success add one row to the state file (`_load_state` / `_save_state`, under `_STATE_LOCK`):

```json
{"name": "torbox-<torrent_id>", "state": "preparing", "title": "<cleaned title>", "kind": null,
 "torbox": {"torrent_id": 123, "hash": "..."}, "release": "<raw release name>", "started": 1794400000,
 "prep": {"progress": 0, "seeds": null, "files": null, "cached": false},
 "dest": null, "bytes": 0, "scanned": false, "scan_error": null, "error": null}
```

  `title` comes from `classify()` on the release name (it already strips the technical tail).
- Answer `{"ok": true, "title": "<title>", "via": "debrid"}`. The panel uses `via` to say where it went.
- A second Get for a torrent whose hash already has a `preparing` row answers ok without adding it again.

### 2. Waiting at the debrid service (in `_debrid_tick()`)

- For each `preparing` row, read the torrent (`mylist?id=...&bypass_cache=true`), at most once every 10 seconds per
  row and at most 6 rows per tick. Update `prep.progress`, `prep.seeds`, `prep.files` (count of media files, see
  below) and save.
- The panel shows these rows as "At debrid" with that progress and a Cancel button. `_debrid_rows()` must pass
  `state`, `title`, `prep` and `name` through, and must drop the `torbox` and `release` fields.
- A row that has not finished at the debrid service after 24 hours becomes `failed` with
  `error: "the debrid service did not finish this torrent in 24 hours"`.
- A torrent TorBox reports as failed (check the real `download_state` values) becomes `failed` with TorBox's text.

### 3. Handing files to the worker

When the torrent is finished and present at TorBox:

- **Pick the files.** Keep files whose name ends in one of `MEDIA_EXT`. Drop any file whose name contains `sample`
  (case-insensitive) or that is smaller than 50 MB when a larger media file exists in the same torrent. No media file
  left: the row becomes `failed`, `error: "this torrent has no media file"`. More than 60 files: take the first 60 by
  name and say so in the row's `error` text.
- **For each file, in name order:** call `requestdl` (space the calls at least 3 seconds apart, across all rows), then
  run the same plan a pasted link gets: `plan_debrid(url)` style logic with the file's `short_name` as the name. When
  the file name alone is ambiguous or lacks a title (episode files inside a pack are often just `S02E03.mkv`), classify
  the **torrent's release name** for the title and kind, and take season and episode from the file name.
- Start it with the worker (`_download_rpc`, `cmd: "start"`) and add a normal download row, exactly like
  `add_debrid()` does, plus `"torbox": {"torrent_id": ..., "file_id": ...}` and **no stored `url`**.
- Hand over at most 2 files per tick so a season pack does not start 20 downloads at once; the rest follow on later
  ticks. Keep the `preparing` row (with `prep.files` showing what is left) until every file is handed over, then
  remove it.
- **Leave the torrent in the TorBox account when done.** (Ryan may later ask for it to be removed; make that one
  constant, `_TORBOX_DELETE_WHEN_DONE = False`.)

### 4. Retry for these rows

`SPEC-downloads-backend.md` task 2 retries with a stored link. A TorBox link has expired by then, so for a row with
`torbox.torrent_id` and `torbox.file_id`: `can_retry` is true in the same three states, and Retry calls `requestdl`
again for a fresh link and starts the worker with it. Nothing else about Retry changes.

### 5. Cancel (`POST /api/debrid` with `{"cancel": "torbox-<id>"}`)

Only for a row in state `preparing`; anything else is a `400`. Remove the torrent from TorBox (`controltorrent`,
`delete`) and remove the row. Files already handed to the worker keep downloading: they are their own rows with their
own Stop. Answer `{"ok": true, "name": "..."}`.

`clear_debrid()` must keep `preparing` rows, the same way it keeps running ones.

### 6. Turn it on

Add `"search"` to `features` in `GET /api/debrid` only when the key file is present **and** this whole flow is
deployed. In the same commit, Get for torrent results takes the new path. Usenet results are untouched.

## Tests to add (fakes only, no real TorBox, Prowlarr or worker calls)

- No key file: Get calls Prowlarr's grab as today and `search` is not in `features`.
- With a key: a magnet result calls `createtorrent` once and adds one `preparing` row; a `.torrent` result uploads
  the bytes; an unusable `downloadUrl` is a 503 and adds nothing; a TorBox refusal is a 503 with its reason.
- `downloadUrl`, `magnetUrl`, the TorBox key, `torbox` and `release` never appear in any HTTP answer.
- Tick: progress is copied into `prep`; a finished single-file torrent becomes one normal row with the right
  destination and the `preparing` row is gone; a 10-episode pack with a sample file hands over 2 files per tick,
  skips the sample, files every episode under the same show folder with the right season and episode, and removes
  the `preparing` row only after the last one.
- `requestdl` is never called with `redirect`; calls are at least 3 seconds apart (inject the clock).
- Retry on a TorBox row requests a fresh link and never reads a stored `url`.
- Cancel of a `preparing` row deletes at TorBox and removes the row; Cancel of any other row is a 400.
- Clear keeps `preparing` rows. The 24-hour limit marks a stuck row failed.

## Rules

- **Tools in Pi Pocket:** there is no `webfetch` or `web_search` tool there; MCP tools are reached through
  `tool_search` / `codemode`. To read a web page or the API description, use `bash` with `curl`.

- Same as `SPEC-downloads-backend.md`: pull and rebase first, small commits, no AI credit, build + `npm run check`
  (`ALL CHECKS OK`) + `npm test` before each commit, deploy with `scripts/deploy.py`, one line per deploy in
  `/mnt/share/hermes-changes.md`.
- **One real end-to-end test is needed at the end, and only Ryan starts it.** When the unit tests pass and it is
  deployed, ask Ryan to press Get on one small, well-seeded result and watch the row. Do not grab anything yourself.
- If the real TorBox API cannot do what a step needs, stop and write down what you found for Ryan.

## How to tell it worked

| Step | What Ryan sees on Media & downloads |
|---|---|
| Get on a Debrid-tab result | Toast "Sent to debrid. It shows under Downloads." and a row "At debrid" with progress and Cancel. |
| The debrid service finishes | That row is replaced by normal download rows (one per file), each filed under Movies or its show folder. |
| A download finishes | "Adding to library", then "In library". |
| A refused torrent | An error message at once, with the debrid service's reason. No nameless red row. |
