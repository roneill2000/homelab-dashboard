# SPEC: GPU session queue (14 Nov 2026, Ryan + Claude)

## Problem
One GPU, one engine loaded at a time, and Strata keeps **one** conversation in its prompt cache. Two sessions working at
once evict each other on every turn: each turn re-reads the whole conversation (10–11 Nov: 45 Flash requests waited
~32 min re-reading 22–56k tokens; cache_n stuck at the shared 8.5k Pocket prefix). A model switch between them adds
~20 s more each time.

## Goal (Ryan)
- **A session holds the GPU while it works.** Other sessions wait, they are not rejected.
- **The hold ends when the session is done or waiting on Ryan**, so a second session can work while the first one sits
  for review. When the first one is resumed it waits its turn like anyone else (its cache is gone by then: accepted).
- **Ryan can write a prompt in Pocket that simply waits** until the GPU is free, then runs. No separate app is needed
  unless the design requires one.
- Side effect wanted: one place showing who holds the GPU, who is queued, and who is waiting on Ryan.
- Phone alerts already exist: `~/.pi-pocket/extensions/rh-ntfy.ts` (question / done / failed).

## Design

### A. Session lease in the GPU broker (`backend/gpu_broker.py`, unified port :8000) — the core
- **One lease for the whole GPU** (only one engine fits; a switch evicts everything anyway).
- **Session identity.** Preferred: an explicit id the client sends (Pocket: see B1). Fallback for clients that send
  none (terminal pi, Paseo, Open WebUI): a key = hash(client IP, model family, the system message, the first user
  message). Verify that the key is stable across turns, across compaction (compaction rewrites history: the summary
  replaces old turns, the system + first message may change) and differs between sessions that share Pocket's system
  prompt.
- **States:** `working` (holder, a turn in progress) → released on **done**, **waiting**, or **idle**:
  - *done*: the holder's response ended with no tool calls (`finish_reason` stop/length): the turn is over.
  - *waiting*: the holder's response called a tool that waits for a person (Pocket `ask`; add others found in the
    tool lists). Pocket approval cards for ordinary tools are invisible to the broker → covered by B1 or by idle.
  - *idle*: no request from the holder for `LEASE_IDLE_S`. **Pick N from data**: from broker history (`ai_requests`)
    and Pocket session logs, measure the gap between consecutive requests *inside one turn* (tool execution time:
    tests, builds, benchmarks); choose N above ~p99 of in-turn gaps (guess: 180–600 s); report the distribution.
- **Waiters:** FIFO by first request. A request from the holder always goes straight through.
- **Side requests must never queue behind their own session** (deadlock): Pocket's background compaction, titles,
  Open WebUI title/tag tasks, loop-guard-free summary calls. Rule to design and test: a request whose session id (B1)
  equals the holder's is the holder's; without an id, a request from the **same client IP while the holder is
  `working`** is treated as the holder's. Document every case decided.
- **Holding a waiting request open:** streaming requests get `200` + SSE headers at once and an SSE comment
  (`: queued, position N`) every 15 s until the lease is granted, then the real stream. **Verify** with pi (pi-ai
  OpenAI client), Pi Pocket, Paseo and Open WebUI that a multi-hour wait does not time out, retry, or show an error
  (find each client's idle/stall timeout in its source). Non-streaming requests: hold up to `QUEUE_HOLD_S`, then
  `503` + `Retry-After`. Status reads (`/v1/models`, `/health`) never queue.
- **Escape hatches:** `QUEUE_ENABLED` (default off until tested), `POST /queue/release` (drop the current lease;
  same-origin + `X-Dashboard-Action: 1` like other actions), lease auto-expires after `LEASE_MAX_S` of continuous
  holding without progress.
- **Unchanged:** wake-on-request, game pause, loop guard, drain-before-swap, `CLIENTS` allowlist, history rows (add a
  `queued_s` column = time spent waiting).

### B. Pocket integration
1. **Tag requests with the Pocket session id.** Find a supported way: `host.shapeModelRequests` (payload only; can a
   field like `metadata.pocket_session` be set per conversation?), a provider registered with
   `registerModelProvider` with per-request headers, or run context. If none works cleanly, fall back to the A key.
   With the id, a drop-in can also release precisely: on `run.ended` and when an approval/ask card opens, call the
   broker's release for that session (more exact than the done/idle inference).
2. **Show the queue in Pocket:** session-row chips (drop-in, `chips` API): "GPU", "Queued #2", "Waiting on you".
3. A prompt written while the GPU is busy needs nothing else: Pocket sends it, the broker holds it (A).

### C. Visibility
- `GET /api/ai/queue` on the dashboard (holder: session label, client, state, since; queue: position, client, age).
- A row on the Local AI page. Optional: ntfy when a queued session starts (off by default).

## Build and test rules
- **The broker serves the agent that builds this.** Develop and test on a second instance on a spare port with stub
  engines (no GPU); the live :8000 keeps running. Unit tests in `tests/test_gpu_broker*.py` with a fake clock:
  grant / queue / done / waiting / idle / side request of the holder / max hold / release endpoint / disabled flag.
- Replay check: run the lease logic over the last 7 days of `ai_requests` and report how many swaps and re-read tokens
  it would have saved, and the in-turn gap distribution (for N).
- Live test only with Ryan's OK, in a quiet window: two Pocket sessions on the Coder + one on Flash; show cache_n hits
  and that a held prompt starts by itself when the first session finishes.
- Ship with `QUEUE_ENABLED` off; Ryan turns it on. Deploy with `scripts/deploy.py` (restarts the broker). Rollback:
  flag off or redeploy the previous commit.
- Review by Claude before the flag goes on.

## Open questions for Ryan (answer when they come up)
- N (idle hold): decided from the data above, Ryan confirms.
- Should Open WebUI chats (short, interactive) skip ahead of queued Pocket work, or wait in line?
- Should a queued Pocket session that has waited more than X hours ping the phone?
