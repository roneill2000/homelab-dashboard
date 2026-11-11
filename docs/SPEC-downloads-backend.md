# Spec: download panel backend follow-ups

Written 11 Nov 2026 by Claude for the local agent (pi). Ryan approved the split: **the screen work is finished and
deployed; this spec is the backend it is waiting for.** Do not change `src/app.jsx` or the CSS for these tasks. When a
task's backend is live, its part of the panel appears on its own.

Files you will touch: `backend/download_proxy.py`, `scripts/homelab_download_lib.py`,
`tests/test_download_proxy.py`, `tests/test_download_lib.py`, `tests/test_debrid_parity.py`.

## How the panel decides what to show

The panel reads a list named `features` from `GET /api/debrid`. It only uses a new call when its name is in that list.
Today the backend sends no `features`, so every new control is hidden.

**Rule: add a name to `features` in the same commit that makes its endpoint work, never earlier.** An older backend
treats `{"url": ..., "preview": true}` as a real download, which is why the panel checks the list first.

```json
GET /api/debrid  ->  {"ok": true, "downloads": [...], "updated": 1794400000, "features": ["preview", "retry", "dismiss"]}
```

All POST calls below keep the existing guard: same origin plus header `X-Dashboard-Action: 1`, JSON body up to 8192
bytes, `403` / `400` exactly as the existing `/api/debrid` POST does. Errors are `{"ok": false, "error": "<one plain
sentence>"}` with the text cut to 200 characters.

## Task 1: preview where a pasted link will be saved (`preview`)

**Why:** the film-or-series guess is sometimes wrong, and today you only find out after the download started.

**Request:** `POST /api/action/download` with `{"url": "<https link>", "preview": true}` and optionally
`"kind": "movie"` or `"kind": "show"`.

**Behaviour:**

- Run everything `add_debrid()` does up to and including `dest = _safe_dest(...)`. **Do not call `_download_rpc`, and
  do not write the state file.**
- When `kind` is given, it wins over `classify()`, the library hint and the SearXNG lookup (skip those lookups).
  `"show"` uses `_safe_show_dir(info['title'])`; `"movie"` uses `MOVIES_DIR`. Any other value is a `400`.
- The same `kind` field is accepted on a real start (`{"url": ..., "kind": "show"}`, no `preview`) and has the same
  effect. Record `kind_source: "user"` in the row in that case.

**Answer (200):**

```json
{"ok": true, "preview": true, "kind": "show", "title": "Game of Thrones", "season": 1, "episode": 3, "year": null,
 "dest": "/mnt/unraid/Shows/Game of Thrones", "name": "Game.of.Thrones.S01E03.2160p.mkv", "kind_source": "name"}
```

`"preview": true` must be present: the panel treats any answer without it as a failed check. A link that does not name
a media file answers `400` with the same sentence `add_debrid()` raises today.

**Suggested shape:** split `add_debrid(url)` into `plan_debrid(url, kind=None)` (returns `url, name, info, dest,
source, grounded`) and a short `add_debrid(url, kind=None)` that calls it and then starts. Preview returns the plan.

**Tests to add:** preview makes no RPC call and leaves the state file untouched; `kind` overrides an ambiguous and an
unambiguous name; a bad `kind` is a 400; a real start with `kind` stores `kind_source: "user"`; a preview of a non-media
link is a 400.

## Task 2: retry a failed download (`retry`)

**Why:** a failed row says "paste the same link to resume", but the link is usually gone by then.

**Store the link:**

- Add `url` to the row `add_debrid()` writes.
- The link carries an access token. **Before storing it, make the state file private:** `_save_state()` must write
  `/var/lib/homelab-dashboard/debrid.json` with mode `0600` (it is `0644` today), and fix the mode of an existing file
  on load.
- `url` must never leave the backend. `_debrid_rows()` (the list `GET /api/debrid` sends) drops it and adds
  `"can_retry": true` when the row has a stored `url` and its state is `failed`, `stopped` or `interrupted`;
  otherwise `"can_retry": false`.

**Request:** `POST /api/debrid` with `{"retry": "<file name>"}` (the row's `name`, checked with `_safe_name`).

**Behaviour:** find the row; refuse (`400`) when it is missing, has no stored `url`, or is not in one of the three
states above. Otherwise call the worker with the stored `url`, `dest` and `name` (`cmd: "start"`, the same call
`add_debrid()` makes; aria2c continues the partial file). On success set `state: "downloading"`, `error: null`,
`scanned: false`, `scan_error: null`, a new `started`, and save. Hold `_STATE_LOCK` around the rows only, never around
the worker call (same pattern as `stop_debrid()`).

**Answer (200):** `{"ok": true, "name": "...", "state": "downloading", "message": "..."}`.

When the worker reports the link is no longer valid (aria2c exit codes 22 or 24 after a retry are the usual sign),
keep the row failed and set `error` to `the link has expired; paste a fresh one` and `can_retry` false (drop the stored
`url`).

**Tests to add:** `url` is absent from every `GET /api/debrid` row; the saved file is `0600`; `can_retry` is true only
for the three states with a stored `url`; retry of a running or finished row is a 400; retry calls the worker with the
stored values and does not hold the lock during the call; a stopped row can be retried (note `stop_debrid()` marks it
`scanned: true`, so reset that).

## Task 3: dismiss a failed search result (`dismiss`)

**Why:** a search grab that RdtClient refuses stays in the list as a red row, and Clear does not remove it.

**Give each row an id:** `recent()` deletes `hash` from its output. Keep it under the name `id` (lower-case hash).
Rows with an empty hash get no `id`.

**Request:** `POST /api/downloads` with `{"dismiss": "<id>"}`. This path has no POST handler today; add one in
`handle()` with the same guard as the `/api/debrid` POST.

**Behaviour:**

- `id` must match `^[0-9a-f]{40}$` (or the 64-character form); anything else is a `400`.
- Read RdtClient's list again and find that hash. Refuse (`400`) unless the item's category is `prowlarr` **and** it is
  in an error state (`state` contains `error` or `missing`, or RdtClient's own record has a non-empty `error`). A
  healthy or running item is never removed by this call.
- Remove it from RdtClient **without deleting data** (the qBittorrent-style call `rdt()` already speaks:
  `/api/v2/torrents/delete` with `hashes=<id>` and `deleteFiles=false`). Check RdtClient's API in the running
  container before relying on that; if it needs a different call, use the one that removes the list entry only.

**Answer (200):** `{"ok": true, "id": "..."}`.

**Tests to add:** `GET /api/downloads` rows carry `id`; dismiss of a running item, of another category, of an unknown
or malformed id are each a 400 and make no delete call; dismiss of an errored `prowlarr` item makes exactly one delete
call with `deleteFiles=false`.

## Task 4: harden the worker (no `features` name; nothing changes on screen)

Both are in `scripts/homelab_download_lib.py`. They were found in review on 11 Nov and are low risk today, so do them
last.

1. **Check redirects.** `safe_url()` tests only the first host; aria2c then follows redirects to anywhere, including a
   LAN address. In `run()`, before starting aria2c, follow the redirects yourself: at most 4 hops, a no-redirect opener
   (the dashboard side already has `_NoRedirect` / `_probe_opener` in `download_proxy.py` to copy), each hop's
   `Location` passed through `safe_url()`. Hand aria2c the final URL and add `--max-redirect=0`. A redirect to a
   private or LAN host fails the job with `the link redirects to a host that is not on the public internet`.
2. **Do not overwrite a finished file.** `ARIA2_FLAGS` has `--allow-overwrite=true`. Change it to `false`. Resuming a
   partial file still works because `--continue=true` uses the `.aria2` control file. `start_debrid()` already answers
   "already finished" for a complete file; add a test that a complete file with no control file is never replaced.

**Tests to add:** a 302 to `192.168.0.4` fails the job and aria2c is never run; five hops fail; a 302 to another public
host passes that final URL to aria2c; `--max-redirect=0` and `--allow-overwrite=false` are in the argument list;
`tests/test_debrid_parity.py` still passes.

## Rules for all four tasks

- Follow the "Git (dashboard repo)" section of `/root/homelab/AGENTS.md`: pull and rebase on `n100` main first, one
  commit per task, no AI credit in commit messages, no secrets in tracked files.
- Before each commit: `npm run build`, `npm run check` (must print `ALL CHECKS OK`), `npm test` (all OK).
- Do not run real downloads, real deletes or a real Retry to test. Use the unit tests with fakes, as the existing
  tests in `tests/test_download_proxy.py` and `tests/test_download_lib.py` do.
- Deploy with `python3 scripts/deploy.py` only after the tests pass, and add one line per deploy to
  `/mnt/share/hermes-changes.md`.
- If something here does not match the code you find, stop and ask Ryan rather than guessing. Write what you found.

## How to tell it worked

After each deploy, open the Media & downloads page:

| Task | What appears |
|---|---|
| 1 | Paste a link: a line "Saves to Shows / <title>" with a Film / Series switch shows before you press Add download. |
| 2 | A failed or stopped row has a Retry button and no longer says "paste the same link". |
| 3 | A red search-result row has a Dismiss button. |
| 4 | Nothing visible. The new tests are the proof. |

`curl -s http://192.168.0.2/api/debrid | python3 -m json.tool | grep -A4 features` shows which names are live.
