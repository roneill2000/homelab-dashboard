# Spec: GPU broker wakes the sleeping gaming PC on demand

Ryan, 12 Nov 2026. Builder: pi. Reviewer: Claude.

## Goal

Ryan wants the gaming PC (.12) **asleep (S3)** when idle (≈ $7–9/month saved; it idles at ~80 W all night today).
Any local-AI request (pi, Pi Pocket, Paseo, Open WebUI, Ask box) through the GPU broker must then **wake the PC itself**
and be answered within about a minute, with no manual Wake.

Part A (wake on request) is the job. Part B (put it back to sleep when idle) is specified but **only build it after
Ryan confirms the idle time and the "someone is at the PC" rule** (ask him; he answers in chat or with `ask`).

## What exists (read these first)

- `backend/gpu_broker.py`: `Handler.forward()` (~line 389). Generation requests (`needs_gpu`) and Strata `/load` call
  `ensure(engine, hold=...)`, which takes `_switch_lock` + `ai_monitor.gpu_switch()` and loads/unloads engines.
  Today a sleeping PC fails as: `ensure()` → exception → **503 "could not switch"**, or the upstream connect →
  **502 "The gaming PC did not answer."** Service `homelab-gpu-broker` on LXC 104, runs as root from `/usr/local/bin`.
- `backend/update_proxy.py` (~line 125): `PC_MAC`, `pc_awake()` (TCP :22, refusal = up), `wake_pc()` (magic packet to
  192.168.0.255 + 255.255.255.255, ports 9 and 7; works from LXC 104), `sleep_pc(force, command)` (forced-command SSH key
  `/etc/homelab-dashboard/pc_sleep_ed25519`: `sleep` = 60 s on-screen warning and refuses if the GPU is busy, `force`,
  `cancel` = cancel a pending countdown). The dashboard's Wake/Sleep buttons use them.
- PC side: llama-server is a Windows service (`llm-router`); Strata tasks `Strata` + `Strata-Coder` have **boot
  triggers** (S4U), so they also come back after a full power-off. After S3 resume all of them simply continue.
  Windows idle sleep is currently **Never** on AC. Wake on Magic Packet is enabled on the 5 GbE NIC.

## Part A: wake on request (build this)

1. **Shared helpers.** The broker must not duplicate the wake code. Move `PC_MAC`, `pc_awake`, `wake_pc`, `sleep_pc`
   into a small module both import, or import them from `update_proxy` if that loads cleanly in the broker process
   (check: no side effects at import, `start_fleet_loop` must not start inside the broker). If you add a module, add it
   to `scripts/deploy.py` (payload list, compile check, install) the same way as `gpu_monitor.py`.
2. **`ensure_awake()` in the broker**, called in `forward()` **before** `ensure()` for `generate or load` requests only.
   Never for `GET /v1/models`, `/health`, status reads, or anything the dashboard / ai_monitor / alerts poll: those must
   keep answering from cache or failing fast, never wake the PC.
   - Fast path: `pc_awake(timeout=1)` true → return immediately (no added latency on an awake PC beyond ~1 ms LAN).
   - Otherwise, under a dedicated `_wake_lock` (one wake at a time; concurrent requests wait for the same wake):
     send `wake_pc()`, then poll every 1 s for up to `WAKE_TIMEOUT = 90` s, re-sending the magic packet every 15 s, until
     `pc_awake()`; then wait (same deadline) until the engine the request needs answers its cheap status URL
     (llama: `GET :8080/health`; Strata/Coder: `GET :8088|8089/health`, the ones `ai_monitor` already uses and which
     don't count as model activity).
   - Log `wake: sent`, `wake: PC up after N s`, `wake: engine ready after N s` (broker `log()`).
   - Timeout → `self.fail(503, 'The gaming PC did not wake up within 90 s. Try again, or wake it from the dashboard.')`
     and record a history row with `swap='wake-failed'`.
   - On success add `'wake'` to the row's `swap` text (e.g. `wake+load-strata`) and include the wake seconds in
     `swap_s`, so the Local AI history shows it.
3. **Pending sleep countdown.** If the PC is awake but a sleep countdown is running (Part B or the dashboard's Sleep),
   a generation request must cancel it: call `sleep_pc(command='cancel')` at most once per 60 s, only when the broker
   knows a sleep was requested recently (track the time the broker itself last asked for sleep; for dashboard-initiated
   sleeps, cancelling unconditionally once per minute while requests arrive is acceptable: the forced command is cheap).
   Never let it delay the request: run it in a thread.
4. **Games.** A game running stays as today (`GamePaused` → 503). Waking does not change that.
5. **Client timeouts.** A wake adds roughly 10–30 s before the usual swap (20–45 s). Measure what each client tolerates
   before the first byte: pi / Pi Pocket (models.json provider), Paseo, Open WebUI (aiohttp client timeout env),
   the Ask box (`qwen_service.py`). Report numbers. Only if one gives up before ~150 s, raise its timeout (back up the
   config first) and list the change. Don't send fake SSE keep-alive bytes before the upstream answers.
6. **Ask box message.** `nightly/qwen_service.py` line ~248 says "The gaming PC is off, asleep… Wake it from Overview";
   with the broker waking it, change the text to say the PC did not wake in time (only shown on the 503 above). Install
   with `python3 nightly/install_qwen.py`.

### Tests (Part A)

Unit tests with the network functions monkeypatched (no real packets, no real PC):
- awake PC → no wake call, request proceeds;
- asleep then up after N polls → one wake, re-send every 15 s, engine health awaited, row `swap` contains `wake`;
- never wakes → 503 after `WAKE_TIMEOUT` (use a tiny timeout in the test), row `swap='wake-failed'`;
- two concurrent generation requests while asleep → exactly one wake sequence;
- `GET /v1/models`, `/health`, non-generation passthrough → never call `wake_pc`;
- cancel-countdown thread is started at most once per 60 s.

`npm run build` → `npm run check` (`ALL CHECKS OK`) → `npm test`.

### Live verification (Part A) — do this with Ryan's OK, at a time he isn't using the PC

1. Dashboard → Sleep the PC (or ask Ryan to). Confirm `pc_awake()` false.
2. From LXC 116: one pi prompt on the 27B (`pi -p "say hi" </dev/null` with the 27B model). Record: time to PC up,
   time to engine ready, total time to first token. Repeat once for Strata Flash (includes a load).
3. Confirm the dashboard and homelab-alerts did **not** wake the PC on their own during 15 minutes asleep beforehand
   (broker log has no `wake: sent` without a matching generation request).
4. Report the numbers. Target: first token ≤ 60 s for the 27B from sleep.

## Part B: auto-sleep when idle (spec only; build after Ryan confirms)

Windows' own idle timer is the wrong tool: it only counts keyboard/mouse input, so it would sleep the PC in the middle of
an overnight pi job. Proposed instead:

- The broker tracks `last_activity` = start or end of any generation request (and any streaming reply in flight).
- A background thread every 60 s: if the PC is awake, nothing is streaming, `now - last_activity >= IDLE_SLEEP`
  (proposed **20 min**, Ryan to confirm), no game is running, and maintenance/snooze is not set to keep it on, call
  `sleep_pc()` (non-forced: the PC refuses while the GPU is busy and shows its 60 s warning).
- **Someone at the PC:** the PC-side `sleep` forced command must also refuse when the console user had keyboard/mouse
  input in the last `IDLE_SLEEP` minutes (`GetLastInputInfo`; note the S4U/session-0 caveat: the check must run in the
  interactive session or read the user's last input another way — investigate and propose before building).
  Backup the PC script first (`*.bak-YYYYMMDD-idle`).
- A dashboard switch "Auto-sleep gaming PC" (default off until Ryan turns it on), state in the existing maintenance /
  settings file pattern; the morning digest says how long the PC slept overnight.
- Never sleep while Ryan's dashboard Wake was pressed in the last `IDLE_SLEEP` (Wake means "keep it on").

## Out of scope

- Waking from full power-off (S5) — WoL from S5 is enabled in the NIC, but Ryan's plan is sleep; test S5 only if asked.
- Changing models, the loop guard, the engine routing or the energy log.

## Deliverables

Commits on n100 main (rebase first, no AI credit, not pushed to GitHub), `scripts/deploy.py` run, test counts, the live
timings from Part A, a line in `/mnt/share/hermes-changes.md`, and a short report saying what you verified and what you
didn't.
