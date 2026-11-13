# Spec: nightly log review, next round (ranking, coverage, history, re-check, feedback, regression set)

Status: for pi or agy to build. Written by Claude, 13 Nov 2026, at Ryan's request ("add as a spec").
Builds on `SPEC-nightly-review-context.md` (done, `82c38d7`) and the Bolt switch (`d2df8e2`).

## Where things stand

- 03:00 on Unraid: `nightly/runner.py` (installed at `/mnt/disk1/.homelab-nightly/`) runs NVIDIA Nemotron 3.5 Bolt
  E4B on the Arc A310 (8k context, one model call per log group, `review_key` repeat reuse).
- The log snapshot comes from Proxmox (`syslog/syslog-monitor.py` → `/api/nightly-snapshot`, grouping in
  `nightly/log_snapshot.py`: key = `(host, level, message)` after scrubbing and dropping `[pid]`; at most 80 groups,
  16 MB read per file).
- `homelab-alerts` pushes new `act` findings once per review date (`nightly_act_notify`).
- Last full run (13 Nov): 8 groups, about 10 min of generation, no act.

## Problems this fixes

1. **Near-duplicates fill the 80 slots.** Any number, IP, port, PID-like id or timestamp inside the message makes a new
   group, so one noisy source can be 30 groups and push out a rare line. Groups are ranked only by level and recency,
   not by how unusual they are.
2. **Coverage holes.** The gaming PC (.12) logs nothing to syslog. `orangepi.log` is 65 MB and `unraid.log` 20 MB
   (13 Nov), so the 16 MB read skips the start of the 24 h window (`window_may_be_incomplete`).
3. **No history.** The model can't tell "first time ever" from "every night for a month". It also can't see what other
   hosts logged at the same moment (a router reboot explains a dozen downstream lines).
4. **Stale act pushes.** An `act` is pushed hours after the line was logged, even when the problem has already cleared.
5. **No feedback loop.** When Ryan decides a finding is fine, nothing records it, so it comes back.
6. **No regression set.** Prompt or model changes are judged by hand each time.

## Changes

### 1. Normalize before grouping, rank by novelty (`nightly/log_snapshot.py`)

- Add `normalize(message)`. After `scrub()`, before the key, replace:
  - IPv4/IPv6 addresses → `<ip>` (keep the last octet for `192.168.0.x`, as `<lan:.x>`, because which LAN host it was
    matters);
  - MAC addresses → `<mac>`;
  - hex ids of 8 or more chars → `<hex>`;
  - UUIDs → `<uuid>`;
  - ISO or syslog timestamps inside the message → `<ts>`;
  - durations and sizes (`12.3s`, `512MB`, `45%`) → `<n><unit>`;
  - any other standalone number of 3 or more digits → `<n>`.

  Keep small numbers (exit codes, `status=143`, `wlan0`, `sdc1`): known causes match on them.
- Group key = `(host, level, normalize(message))`. Keep `message` = the latest raw (scrubbed) example, and add
  `variants`: the number of distinct raw messages folded into the group, plus up to 3 examples.
- Group `id` must stay stable for the same normalized key, so repeat reuse keeps working. It will change once for
  existing groups; that's fine.
- **Baseline.** Proxmox keeps `/var/lib/syslog-monitor/baseline.json`, which `syslog-monitor` writes after each
  snapshot. It holds, per normalized key, `nights_seen` (last 30 nights, as a bitmask or a list of dates) and the
  median nightly count. Bound it to 5,000 keys; when it's full, evict the keys not seen for the longest time.
- Each group gets `nights_seen_30` and `baseline_count`. Rank before the 80-group cut: CRITICAL first, then
  `novelty = (nights_seen_30 == 0) * 3 + (count > 3 × baseline_count) * 2 + (hours with lines ≥ 6) * 1`, then
  `last_at`. A group seen 30 of 30 nights at its usual count goes last.

### 2. Coverage

- **Read window:** raise `MAX_BYTES` to 32 MB, or better, seek by timestamp. Binary-search the file for the first line
  at or after the window start, then read from there. Either way, `coverage[].window_may_be_incomplete` must be false
  for orangepi and unraid on a normal night. Measure the snapshot time on Proxmox (it's an N100); it must stay under
  30 s.
- **Why those logs are so big:** list the top 5 normalized messages by volume in `orangepi.log` and `unraid.log` and
  put them in the report. If it's a repeating harmless line (e.g. a nebula-sync or Kuma heartbeat), propose an rsyslog
  filter on the sender. **Don't apply it without Ryan's OK.**
- **Gaming PC (.12):** propose a forwarder; **don't install** without Ryan's OK. Preferred: a scheduled task every
  15 min that runs `Get-WinEvent` for System + Application, Level ≤ 3 (critical, error, warning), since the last run.
  It writes to `C:\llm\eventlog-out\` as JSON lines, and Proxmox pulls with `scp pc:` into `/var/log/remote/gaming-pc.log`
  in syslog format. Pull, not push: the PC's SSH key is `from=` restricted and a pull needs no new listener. Filter out
  the known-noisy providers (list them from one day's sample). The PC is often asleep: a gap is "quiet", not an error.

### 3. History and cross-host lines (`runner.py` `request_body`)

- Send `nights_seen_30` as a line such as "seen on N of the last 30 nights (first seen YYYY-MM-DD)". Add to the
  instructions: "A line seen on most nights is the usual pattern. Say so, and only rate it above ignore if the count
  or the lines changed."
- **Cross-host:** for each group, find up to 4 lines from *other* hosts within ±60 s of its latest occurrence that are
  themselves in a group, or match `BOOT_LINE`, link up/down, or `Started`/`Stopped`. Send them as `nearby` (scrubbed,
  300 chars). Build this on Proxmox in the snapshot, not on Unraid, because only Proxmox has all the files.
- Mind the 8k context: measure the longest request. If it no longer fits, drop `nearby` first, then cut `context` to
  6 + 3 lines.

### 4. Live re-check before an act push (`alerts/homelab_alerts.py`)

- Before sending, for each `act` finding, ask `syslog-monitor` for a new endpoint `/api/group-recent?id=<id>&since=<ts>`:
  how many lines matching the normalized key since the review's snapshot time. Bound it to the last 2 MB per file.
- If it is still happening (count > 0), push as now, adding "still happening: N since 03:xx".
- If it has stopped, push at priority 3 (not 4), with "not seen since HH:MM". Never drop it silently: an act like
  pending disk sectors matters even when quiet.
- If the endpoint fails, push as now (fail open).

### 5. "This is fine" button

- Dashboard: on each nightly finding (`src/app.jsx`, the nightly findings list), add a small "This is fine" action.
  It goes through the existing action guard (same-origin + `X-Dashboard-Action: 1`). It asks for an optional one-line
  reason and POSTs `/api/action/nightly-fine` with `{id, host, normalized message, reason}`.
- Backend (LXC 104): append to `/var/lib/homelab-dashboard/nightly-fine.json`, with no more than 200 entries.
- `runner.py` reads that list (fetch it from `http://192.168.0.2/api/nightly-fine` with the same pattern as
  `/api/storage`). For a group whose normalized key is on it, it adds the note "Ryan marked this as fine on
  YYYY-MM-DD: <reason>". It doesn't skip the group: if the lines change, the model can still say so. Include the note
  in `review_key`, so a newly marked group is reviewed once more.
- Undo: the button toggles. Show "marked fine" on the finding.
- **No free-text reason reaches the model unscrubbed:** run `scrub()` on it and cut it to 200 chars.

### 6. Regression set (`nightly/regression/`)

- `events.jsonl`: the 15 hard events from `/mnt/share/ai/a310-review-models-20261113/round4/events.jsonl`, plus the
  10 round-2 events (`round2/`). Each event keeps its exact group dict, so it goes through `request_body` as live.
- `expected.json`: for each event, the acceptable verdicts and 1–3 key phrases (regex, any one must match), plus
  forbidden phrases (the traps). Build it from the round-4 rubric, which Claude will hand over: ask for it. Don't
  invent expectations from model answers.
- `run_regression.py`: starts the A310 container exactly as `runner.py` does (`container_command`, its own container
  name, port 11438). It runs each event once at seed 42, scores 2/1/0 by the rubric rule (verdict acceptable + key
  phrase = 2; one of the two = 1; a forbidden phrase or two steps off = 0), and prints a table and a total. It writes
  `results-<date>-<PROMPT_VERSION>.jsonl` under `/mnt/share/ai/nightly-regression/`.
- Pass bar for any future prompt or model change: total ≥ the current Bolt baseline (record it in the first run's
  commit message) and no new 0 on h5, h7, h8, h15 (disk, filesystem, SSH flood, NVMe).
- It obeys the same A310 rules as the nightly: `run.lock`, never between 02:45 and 04:00 or between 06:45 and 07:15,
  and it removes its container at the end.

## Constraints

- The A310 only. Never call the GPU broker (:8000) or the gaming PC from the review or the regression run.
- Only scrubbed text reaches the model, the report or ntfy. Credentials, the ntfy topic and the WAN IP stay out of
  tracked files.
- Report ≤ 64 KB (`save()`); `baseline.json` ≤ 2 MB.
- Install: `python3 nightly/install_runner.py` (Unraid), `python3 nightly/install_snapshot.py` (Proxmox
  syslog-monitor; check what it copies and restarts), `scripts/deploy.py` (dashboard + backend), and
  `/usr/local/bin/homelab-alerts` with a `.bak-YYYYMMDD-<topic>` backup. Bump `PROMPT_VERSION` because the
  instructions change.
- Items 2 (rsyslog filter, PC forwarder) need Ryan's OK before anything is installed. Write the proposal into the
  report and stop there.
- Commits: no AI credit, existing git author, `git pull --rebase n100 main` first. Don't push to GitHub.

## Order and done when

Build in this order, one commit each, with tests (`tests/test_nightly.py`, `tests/test_snapshot_redaction.py`,
`alerts/test_alerts.py`). Then `npm run build`, `npm run check` (ALL CHECKS OK) and `npm test`.

1. Normalize + baseline + novelty ranking. Test: two lines that differ only by IP and port → one group, `variants`
   2; `status=143` vs `status=3` stay separate; ranking puts a never-seen line above a nightly one.
2. Regression set + one baseline run on the current prompt. Do this before items 3–5, so their effect can be measured.
3. History + cross-host lines. Rerun the regression; it must not get worse.
4. Read window (seek by timestamp); the coverage report for orangepi and unraid is complete.
5. Live re-check before act pushes.
6. "This is fine" button (preview check of the dashboard, at 412 px dark and 1440 px light, with mocked data).
7. Proposals only: the rsyslog filter and the PC event-log forwarder.

Then one manual nightly run on Unraid outside the blocked windows. Report the groups before and after
normalization, the snapshot time, the request token counts (longest), the regression score per item, and the
commit hashes.
