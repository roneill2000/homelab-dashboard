# Spec: downloads panel augmentations (11 Nov 2026)

Five additions to the Downloads panel, in build order. Each is independent and ships on its own commit.
Follows `SPEC-downloads-backend.md` and `SPEC-search-via-debrid.md`; the rules there still hold (no key or link
ever leaves the backend, state file 0600, the worker takes one JSON request and no shell).

**Feature flags.** As before, the panel shows a new screen only when `GET /api/debrid` lists its name in
`features`. New names: `space`, `notify`, `cached`, `queue`, `history`. An old backend must never be sent a
request it would read as a real download.

**Fix first (not part of this spec, but blocking):** the Cancel-race orphan download (review item 4) and
`--allow-overwrite=true` in the worker. The queue in task 4 makes both more likely to bite.

---

## Task 1: space check before a download starts (`space`)

**Why:** grabs are 30-90 GB and the array has under 1 TB free. Nothing warns today.

**Worker** (`scripts/homelab_download_lib.py`, `rpc_reply`): new read-only request
`{"op": "space", "dest": "<folder>"}` -> `{"ok": true, "free_bytes": N, "total_bytes": N}` from
`os.statvfs` on the destination after `safe_dest`. Measuring on the worker gives the number the share really
reports (the share may land on the cache pool or the array; do not guess from the dashboard's disk tiles).
Cache the answer 30 s per folder root.

**Backend** (`backend/download_proxy.py`):
- Preview answers (pasted link and search result) gain `size_bytes` (from the link's `Content-Length` or the
  indexer result; `null` when unknown), `free_bytes`, and `free_after_bytes`.
- A real start is refused with 409 and a plain sentence when `size_bytes` is known and
  `free_after_bytes < RESERVE_BYTES` (constant, 50 GB). Downloads already running count against free space:
  subtract their remaining bytes.
- Unknown size never blocks; the preview says the size is not known.
- If the `space` call fails, the preview still answers, with `free_bytes: null`. Fails open.

**Panel:** the preview line reads "88 GB · leaves 861 GB free". Under 100 GB left after: warning colour.
Would not fit: the start button is disabled and the line says why.

**Tests:** fits / does not fit / unknown size / worker call fails / running downloads reduce free space;
`space` rejects a destination outside the share roots.

---

## Task 2: notification when a download ends (`notify`)

**Why:** today the only way to know is to look at the panel.

- Sender: the dashboard backend on LXC 104, from the existing tick, on a row's state change. One message per
  download, sent once (store `notified: "<state>"` on the row so a restart does not repeat it).
- **Finished:** sent when the row reaches the point the panel calls done, meaning Jellyfin has the item (not
  when the bytes land). Title "Added: <clean title>", body "<size> in <duration> · <folder>". Priority 2.
- **Failed / interrupted:** title "Download failed: <clean title>", body = the same reason sentence the panel
  shows. Priority 3. Not sent for `stopped` (the user did that).
- A multi-file grab (a season) sends one message for the whole grab, when its last file ends:
  "Added: <show> season 2 · 10 files · 34 GB". If some files failed: "8 of 10 files".
- Headers: `Click: homelab://view/media`, `X-Sequence-ID: dl-<row id>` so a later retry's result replaces the
  failure.
- Topic and server: read from a private file under `/etc/homelab-dashboard/` (0600). Never in the repo, the
  state file, logs or an API answer. If the file is absent the feature is off and `notify` is not listed.
- Alert snooze does not hold these (they are answers to something the user started).
- Add a row to `alerts/SENDERS.md`.

**Tests:** one message per download across a restart; season grouping; `stopped` is silent; a send failure is
logged once and does not block the tick; the topic never appears in `GET /api/debrid`.

---

## Task 3: "ready now" mark and a suggested pick in search results (`cached`)

**Why:** the most useful fact about a result is whether the debrid service already has it (starts in seconds)
or has to fetch it (minutes to never).

- **Real-Debrid cannot tell us:** `instantAvailability` is disabled (error 37, see the search spec). Do not
  try to work around it by adding torrents to probe.
- **TorBox:** has a cached-check call by info hash (`/torrents/checkcached`). **Unverified: confirm the path,
  parameters, batch size and answer shape against the live API before building**, and write them into the
  API table in `SPEC-search-via-debrid.md`.
- Backend: after a search, collect the info hashes the indexer gave (results without a hash are skipped, not
  fetched), ask TorBox once for the batch, and add `cached: true | false | null` to each result. `null` =
  not known. Time limit 3 s; on timeout or error all results get `null` and the search still answers.
- `cached: true` only says TorBox has it. The grab order stays Real-Debrid first; do not reorder providers
  because of this mark. (Real-Debrid usually has what TorBox has; the mark is a hint, and the label says so.)
- **Suggested pick:** the backend marks at most one result `suggested: true`: among results with
  `cached: true` (or all results when none are known), prefer 2160p over 1080p, then the most seeders, and
  skip CAM/TS and anything over `SUGGEST_MAX_BYTES` (constant, 100 GB). No suggestion when nothing qualifies.

**Panel:** a small "Ready now" tag on `cached: true` rows, nothing on `false`/`null`; "Suggested" tag on the
one pick, which also sorts first. Tooltip on Ready now: "Already at TorBox; usually instant."

**Tests:** batch call made once per search; timeout gives `null` everywhere and a normal search answer; no
hash = no lookup; suggestion rules (resolution, CAM filter, size cap, none).

---

## Task 4: queue with a limit (`queue`)

**Why:** three downloads at once share one 2.3 Gbps line and one array disk; each is slower and the disk
thrashes.

- `MAX_RUNNING = 2` (constant). Counts worker units in state `downloading`, from pasted links and grabs alike.
- A start request over the limit creates the row in a new state **`queued`** and answers 200 with
  `"queued": true`. Add `queued` to `_DEBRID_STATE_WORDS`; the worker does not need to know the state.
- The tick starts the oldest queued row when a slot frees. Space (task 1) is checked again at that moment; if
  it no longer fits the row becomes `failed` with the space sentence.
- **Links can expire while waiting.** For a grab, do not ask the service for the download link until the row
  leaves the queue (the existing hand-over step already works per file, so queue the hand-over, not a link).
  For a pasted link, a start that fails with the expired-link pattern (`_EXPIRED_LINK`) gets the existing
  expired wording.
- A season grab's files count toward the limit one by one (so a 10-file season runs 2 at a time and a second
  request waits its turn per file, oldest row first).
- Cancel on a queued row removes it with no worker call. Retry on a failed row goes through the queue.
- The queue lives in the state file and survives a restart.

**Panel:** queued rows sit under the running ones: "Waiting · 2nd in line", with Cancel. No reorder control.

**Tests:** third start is queued; slot frees -> oldest starts; restart keeps order; cancel while queued makes
no RPC call; space re-check at start; season files respect the limit; the Cancel-race fix holds with queued
rows.

---

## Task 5: history (`history`)

**Why:** "how fast was that, when did I get it" without reading job files.

- `GET /api/debrid?history=1` -> `{"ok": true, "history": [...]}`: the last 20 ended rows (any end state),
  newest first: `title`, `kind`, `folder`, `state`, `size_bytes`, `started`, `finished`, `seconds`,
  `avg_bps`, `provider` (Real-Debrid / TorBox / link), and `reason` for failures. No link, hash or unit name.
- Source: the dashboard state file. Raise `_DEBRID_KEEP` to 100 and keep ended rows for history for 30 days;
  the 7-day rule for the main list is unchanged. Rows cleared from the list (Clear) stay in history: mark
  them `cleared`, do not delete.
- Season grabs: one history entry per grab with file count and total size.

**Panel:** "History" link on the Downloads heading opens a sheet: one line per entry, "Dune · 88 GB · 5 min 33 s
· 265 MB/s · Real-Debrid · 11 Nov 09:27". Failures in the error colour with the reason. No actions.

**Tests:** cleared rows remain; 30-day and 100-row limits; nothing secret in the answer; average speed for a
row with unknown size is `null`.

---

## Out of scope

Magnet paste, the RdtClient list, series monitoring (Sonarr's job), reordering the queue, per-download speed
limits.

## Done means

For each task: `npm run build`, `npm run check` (`ALL CHECKS OK`), `npm test`, the feature name in `features`,
and one real download watched end to end with the panel open (Ryan present; tests never start real ones).
