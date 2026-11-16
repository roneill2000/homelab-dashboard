# Spec: four UI bugs from the 15 Nov UI audit

Status: for pi. Written by Claude, 15 Nov 2026, at Ryan's request. Each bug below was confirmed by Claude in the source
and, where it says so, on the live dashboard. Audit reports with screenshots:
`/mnt/share/notes/reports/agy-ui-20261115/{phone,desktop}/REPORT.md`.
Already fixed by Claude in `35faed6` (do not redo): phone search-tag wrapping, Find-a-service web row, request-table
scroll, GPU queue list, loop-guard label, reduced-motion live dot, heading outline.

## Rules

- Work in your own worktree off n100 main; `git pull --rebase n100 main` before you start and before you merge.
- `npm run build` → `npm run check` (must print `ALL CHECKS OK`) → `npm test`, every command under `timeout -k 10 N`.
- Each fix gets a test that FAILS with the fix reverted (say which file/test and show the failing run).
- No AI credit in commits. Never push to GitHub. Merge to n100 main only when everything is green. Do not deploy;
  Claude deploys (a full `deploy.py` restarts the GPU broker and cuts running sessions).
- Never click real actions: browser checks use the preview (`scripts/preview.py`, 127.0.0.1:8799, refuses POSTs).

## 1. Download history is always empty (high)

`src/app.jsx` `toggleHistory()` (~line 1141): `setHist(r&&r.ok?(r.history||[]):false)` reads `.history` from the
fetch `Response`, never from its JSON, so the list is always empty. Live `GET /api/debrid?history=1` does return items
(e.g. `{"ok":true,"history":[{"title":"Severance","finished":1794455348,...}]}`).
Second trap: the history row (~line 1187) calls `when(h.finished)`, but no `when` exists in `DownloadsPanel`'s scope
(the ones at ~133, ~152, ~1202 belong to other components; the one in `grabs.forEach` is local to that loop). Fixing only
the JSON read would throw a ReferenceError and take down the Media page section.
Fix: `await r.json()` (catch → `false`), and a local time formatter for `finished` (seconds since epoch).
Test: a browser check with a mocked `/api/debrid?history=1` returning 2 rows → both titles visible, no console error.

## 2. Quick actions: disabled look and the snoozed highlight are overridden (high)

`src/m3-controls.css` line ~230 `.ov-actions .quick-actions>.m3-btn{… background:secondary-container; color:…}` has
specificity (0,3,0) and beats both `.m3-btn:disabled` (0,2,0, ~line 184) and `.quick-actions .is-snoozed` (0,2,0,
`src/styles.css` ~911). Result: a disabled quick action looks enabled, and while alerts are snoozed the
"Snoozed until … · Resume" button looks like every other button.
Fix: give the disabled and `.is-snoozed` states rules at least as specific as line 230 (or wrap line 230's selector in
`:where()`), keeping the current look of normal buttons.
Test: browser check that reads computed background of (a) a disabled quick action vs an enabled one (must differ) and
(b) `.is-snoozed` (must be the tertiary-container colour). Mock `/api/maintenance` for the snoozed state (the preview
does not serve that endpoint; the live dashboard does).

## 3. "Last reading Invalid Date" on the Local AI GPU panel (medium; visible live on 15 Nov)

`src/app.jsx` `PcGpuPanel` (~line 325): `status=fresh?'Live':g?\`Last reading ${new Date(g.observed_at*1000)…}\``.
Live `pc_gpu.latest` is `{}` (truthy, no `observed_at`), so it prints "Last reading Invalid Date".
Fix: use the reading only when `valid(g?.observed_at)`; otherwise "Unavailable". Check the tiles and chart do not show
stale or NaN values in that case either.
Test: unit/browser check with `latest: {}` → text "Unavailable", never "Invalid Date".

## 4. Local AI contradicts itself when llama-server is down but Strata is up (medium)

Seen by both agents: the Local AI card shows "LOADED NOW · Qwen3.8 Flash · Open chat" and, right under it,
"Gaming PC unavailable … Wake it to chat" with a Wake PC button. The Ask AI dropdown shows the heading
"Gaming PC · off" with Qwen3.8 Flash still selectable under it. Cause: the "PC unavailable/off" wording is driven by
`ai.desktop` (llama-server on :8080) only, while Strata (`ai.strata`) and the Coder (`ai.coder`) are separate servers on
the same PC that can be up while llama-server is down (e.g. game-watch, llm-router stopped).
Fix: derive "PC reachable" from any of `ai.desktop.online`, `ai.strata.online`, `ai.coder.online`; say "llama-server
offline" (not "Gaming PC unavailable / wake it") when only llama-server is down; group headings in the Ask dropdown
must match the options' enabled state. Keep the Wake PC button only when every engine is unreachable.
Test: browser check with mocked telemetry `desktop.online:false, strata.online:true, strata.loaded:true` → no
"Gaming PC unavailable", no "Gaming PC · off" heading above an enabled option.

## Report

Short sentences: commit hashes, each failing-then-passing test, `ALL CHECKS OK` and test counts, and what you did not check.
