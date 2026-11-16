# Spec: follow-up questions in the Ask AI box (per-tab chat history)

Status: for pi. Written by Claude, 15 Nov 2026, at Ryan's request ("Can we make the Ask AI homelab have chat history at
least in that session? It can clear it out after the page is closed").

## Goal

The dashboard's Ask AI box (Overview and Local AI) answers one question at a time today: every question starts a new
conversation. Make follow-ups work: the model sees the earlier questions and answers of this browser tab. The history
lives only in the tab and is gone when the tab (or the Android app's WebView) is closed. Nothing is stored on a server.

## Where things are today (read first)

- `src/app.jsx` `AskQwen` (~lines 390–460): `POST /api/qwen` with `{prompt, target, homelab}` and header
  `X-Qwen-Request: 1`, then polls `GET /api/qwen` for the job (`status`, `answer`, `model`, `context_note`, …).
- `backend/qwen_proxy.py` on the dashboard (LXC 104) forwards to the service on Unraid; it refuses bodies over
  16,384 bytes (`0<length<=16384`).
- `nightly/qwen_service.py` on Unraid (installed by `nightly/install_qwen.py`): `submit(prompt, target, homelab)`
  validates (prompt 1–3,000 chars, `TARGET_RE`), scrubs the prompt, runs one job at a time (`run.lock`, shared with the
  nightly review). Messages are built around line ~112 (`messages=[dict(role='user',content=prompt)]` or the homelab
  observation variant). The A310 path counts tokens with `/apply-template` and refuses over its 6,144-token context;
  the PC path allows 32k. `PLAIN_NOTE` / `STRATA_NOTE` tell the user what was sent ("no … chat history …").
- Targets: `a310` (Nemotron Bolt E4B on Unraid), `pc…` (llama-server models on the gaming PC), `strata` (Flash).

## Changes

### Page (`src/app.jsx`)
1. Keep the conversation in `sessionStorage` (key e.g. `ask-history-v1`): a list of `{q, a, model, at}` turns, newest
   last. Survives a reload and page switches inside the tab; gone when the tab closes. Wrap every read/write in
   try/catch (sessionStorage can throw); without it the box works as today, history in memory only.
2. Show the earlier turns above the input (question, answer, model name), newest at the bottom, each answer rendered
   the same way as today's single answer. Long histories scroll inside the card; do not push the input off-screen on a
   412 px phone.
3. A "New chat" button (only when there is history) clears it. Switching the model keeps the history (the new model
   gets it), and the UI says so in one short line.
4. Send `history: [{q, a}, …]` with the new question. Answers only: never the model's thinking. Cap what is sent at
   10,000 characters total (oldest turns dropped first), so `prompt + history` stays under the proxy's 16 KB limit.
5. "Include homelab data" applies to the new question only (as today).

### Service (`nightly/qwen_service.py`)
6. `submit()` accepts an optional `history`: a list of at most 20 items, each a dict with string `q` and `a`
   (≤ 3,000 and ≤ 6,000 chars). Anything else → 400 with a clear message. Scrub every `q` and `a` with the same
   `scrub()` used for the prompt.
7. Build `messages` as alternating `user`/`assistant` turns from the history, then the new question (with the homelab
   observations when asked, exactly as today). No system prompt is added (Ryan's rule since 30 Oct).
8. Fit to the target's context: if the conversation is too long (A310: count with `/apply-template` as today; PC/Strata
   by their limits), drop the oldest turns until it fits. If even the new question alone does not fit, keep today's
   error. Report how many turns were sent and dropped in the job (e.g. `history_sent`, `history_dropped`).
9. Update the context note: say how many earlier turns were included, e.g. "Your question and 3 earlier turns were
   sent; no system prompt or homelab data." and "2 older turns were left out to fit the A310." Keep PLAIN_NOTE for a
   question with no history.
10. The nightly review, alert "Ask AI" and the 07:00 digest use the same service: they send no `history`, and their
    behaviour must not change.

### Proxy (`backend/qwen_proxy.py`)
11. No change needed unless you find the 16 KB cap too tight in testing; if you raise it, keep a hard cap (≤ 32 KB).

## Tests (each must fail with its change reverted)
- Service: history validation (bad types, too many turns, too long) → 400; messages alternate correctly; scrub applied
  to history; oldest turns dropped to fit (fake token counter); a request without history builds exactly today's
  messages (byte-identical) so nightly/digest/alerts are unchanged.
- Page (browser check on the preview, mocked `/api/qwen`): a second question sends the first Q/A as history; "New
  chat" clears it; a reload keeps it; a new browser context (= closed tab) starts empty; the request body never
  contains thinking text; the 10,000-char cap drops the oldest turns.

## Rules
- Own worktree off n100 main; `git pull --rebase n100 main` before you start and before you merge.
- `npm run build` → `npm run check` (`ALL CHECKS OK`) → `npm test`, each under `timeout -k 10 N`.
- No AI credit in commits. Never push to GitHub. Merge to n100 main only when green. Do not deploy and do not run
  `install_qwen.py`: Claude installs (the service is live on Unraid and shared with the nightly review).
- Never call the real Ask service (`/api/qwen` on 192.168.0.2 or Unraid :11438) from tests: the A310 run lock is shared
  with the nightly review. Mock it.

## Report
Short sentences: commit hashes, the failing-then-passing tests, gate results, what a 3-turn conversation sends on the
A310 (token count from a mocked `/apply-template` is fine), and what you did not check. Then stop.
