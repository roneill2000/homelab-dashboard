# Spec: nightly log review, part 2. Better inputs, then a 3-model test on the A310

Written by Claude, 13 Nov 2026, for agy (Ryan's request). It follows `docs/SPEC-nightly-review-context.md` (built in
`82c38d7`, known causes extended in `abd7a9c`).

Two parts, in order:
- **Part A** changes the live review and is installed when done.
- **Part B** is a test only. It changes nothing live, and you **don't judge the results**: Claude grades them.

## Facts

- The review runs on Unraid (`ssh root@192.168.0.4`) at 03:00 from `/mnt/disk1/.homelab-nightly/runner.py`, in the Docker
  image `homelab/llama-sycl-a310:b11804` on the Arc A310 (4 GB). Container name `homelab-nightly-llm`, port 127.0.0.1:11437.
- Today's model: Gemma 4 E4B Q4_K_M, 8192 context, `--reasoning-budget 768`, `max_tokens 1152`. It uses 3,245 MiB of
  4,048 at peak. A full run (5 events) takes 293 s.
- Models live in `/mnt/cache/appdata/homelab-models/`. The A310 build script is referenced in `runner.py`
  (`evals/a310-backends-2026-10-22/build_aot.sh`; find it on Unraid).
- The log snapshot runs on Proxmox (`nightly/log_snapshot.py`, installed into `/usr/local/bin/syslog-monitor.py` by
  `nightly/install_snapshot.py`). `snapshot(directory, critical, warning, now=...)` takes a `now`, so a past night can
  be replayed from the retained logs in `/var/log/remote/` (plus `.log.1`).
- The Ask box on Unraid (`qwen_service.py`) also uses the A310, on demand. Only one model fits on the card at a time.

## Part A: better inputs (live)

1. **Code decides routine verdicts.** Add an optional `"verdict"` (`ignore` / `watch` / `act`) to entries in
   `nightly/known_causes.json`:
   - `ignore`: `dl-*` (exit 143 only), `bench-*` / `agy-*` / `pi-queue`, and `gl-be9300` wlan down/up.
   - None for the others (OOM, Scrutiny, Orange Pi DNS): the model decides those.

   When exactly one matching entry has a verdict, the finding takes it, whatever the model said. Keep the model's
   text, and store `verdict_source: "rule"` or `"model"`. If two matching notes disagree, let the model decide and log
   it. Show `rule` as a small marker next to the badge in the dashboard.
2. **Same-time lines from other hosts.** For each event, add up to 6 lines from *other* hosts' logs within ±120 s of
   its latest occurrence. Prefer lines that match the warning/critical patterns, otherwise the nearest ones. Scrub them
   with `scrub()` and cut each to 300 chars. Send them as `other_hosts` and tell the model they may or may not be related.
3. **History.** From the saved reports (`reports/report-*.json`, last 30), add
   `history: {"nights_seen": N, "of_last": M, "last_verdict": ..., "last_seen": "<date>"}` per group id. Tell the model
   that a long history with the same verdict means it's a known pattern.
4. Keep the prompt within the 8192 context. Measure the longest real prompt, and if it's over ~6,500 tokens, cut
   `other_hosts` first, then `context`.
5. Tests for each part, then `npm run build`, `npm run check` (ALL CHECKS OK), `npm test`, and
   `python3 -m unittest tests.test_nightly`. Commit (no AI credit, existing git author) and install
   (`install_snapshot.py`, `install_runner.py`; both back up first). Run the review once by hand and report the findings.
   **Deploy the dashboard part only when no `bench-*` unit is active on Proxmox** (deploy restarts the GPU broker).
   Otherwise leave it for later and say so.

## Part B: model test (nothing live changes)

Work in `/mnt/share/ai/a310-review-models-20261113/` (create it). Put scratch files there or in `/tmp`.

1. **Replay set.** Build the nights from the retained logs with Part A's pipeline. Use every night the logs fully
   cover (at least 7 if they exist). Run `snapshot(..., now=<that day 03:00 local>)`, then `prepare_evidence` as
   `runner.py` does. Keep up to 8 events per night, up to 40 events in total, mixed across hosts. Save them as
   `replay.jsonl` (scrubbed evidence only). Don't use history from reports newer than the replayed night.
2. **Arms.** Each arm uses the same evidence, the same instructions and the same JSON schema as Part A:
   - **A: Gemma 4 E4B Q4_K_M.** Today's model and settings.
   - **B: Qwen3.5-4B (official, Qwen org).**
     - **File:** a Q4_K_M GGUF from Qwen's own repo, or from unsloth/bartowski if Qwen has none.
     - **Pin it:** record the repo, the commit and the sha256.
     - **Sampling:** the card's thinking-mode values, `--reasoning-budget 768`.
   - **C: IFM/K2-Horizon-3.7B** (https://huggingface.co/IFM/K2-Horizon-3.7B, Apache-2.0).
     - **File:** the card lists community quants. Only use one from a known quantizer (unsloth, bartowski,
       mradermacher, lmstudio-community, or IFM itself). Pin the commit and sha256, and check that the GGUF's
       architecture is one llama.cpp actually supports.
     - **No good quant, or llama.cpp doesn't support it** (even after the rebuild in step 3): stop arm C and report
       why. Don't convert unsupported weights with custom code.
     - **Settings:** the card expects reasoning effort "high" and 32k output, which doesn't fit here. Run C twice:
       C1 with `reasoning_effort` low and budget 768 (same as the others), and C2 with effort high, budget 4096 and
       `max_tokens` 6000, on the first 10 events only, with a 30-minute cap for C2 in total.
3. **Engine.** Try the existing `homelab/llama-sycl-a310:b11804` image first. If an arm's architecture isn't
   supported, build a **new** image with a newer llama.cpp release tag, using the same build script and SYCL AOT for
   acm-g11, and tag it `homelab/llama-sycl-a310:<tag>`. **Never retag or replace `b11804`:** the live review uses it.
   Run arm A on the new image too, so engine differences show.
4. **Fit.**
   - **Settings:** every arm runs at 8192 context with the same flags as `runner.py`'s `container_command`
     (`per_layer_token_embd=CPU` applies only to Gemma).
   - **Limit:** peak VRAM must stay ≤ 3,700 MiB. Record it.
   - **If an arm doesn't fit:** try IQ4_XS, then Q3_K_M, and record which quant was used.
   - **RAM:** don't go over the existing `--memory 7g` container limit.
5. **When it may run.**
   - **Clear of other jobs:** not between 02:45 and 04:00, or between 06:45 and 07:15 (03:00 review, 07:00 digest).
   - **Card must be free:** check before each arm that no other container holds the A310 (`docker ps`; the review
     and Ask containers). If one does, wait or stop and report. Don't kill it.
   - **Use a different name:** run your containers as `a310-review-test` on port 127.0.0.1:11438, never the live
     container name or port. Remove them when done.
6. **Record per event and arm** in `results.jsonl`:
   - arm, event id, the raw reply, the parsed JSON (or the parse error);
   - `prompt_tokens` / `completion_tokens`;
   - generation tok/s, wall time, and whether thinking hit the budget.

   Per arm in `arms.json`: model repo, commit, sha256, quant, image tag, load time, peak VRAM, total time, and parse
   failures.
7. **Blind sheet for grading.** Write `grading.md`: each event's evidence once, then the answers from each arm in
   random order labelled X/Y/Z (W for C2). Put the key mapping labels to arms in `key.json`, which `grading.md` must
   not reveal. **Don't grade, rank or recommend.** Claude grades.

## Rules

- Never call the GPU broker (192.168.0.2:8000) or the gaming PC. This is A310 only.
- Unraid: back up any file you replace (`*.bak-20261113-models`). Downloads only go to
  `/mnt/cache/appdata/homelab-models/<model>/`. Check free space first and stop if there's under 20 GB free.
- Don't change the live model, the cron or the live image in Part B.
- No secrets in files or reports. Evidence must be scrubbed.
- Don't leave anything running or watching. Remove test containers at the end.

## Report

Write it to `/mnt/share/ai/a310-review-models-20261113/REPORT.md`, with:
- Part A: commit hash(es), install backups, and the findings from the manual run.
- Part B:
  - per arm: quant, image, VRAM, load time, total time, tok/s, parse failures and budget hits;
  - why any arm was stopped;
  - where `grading.md` and `key.json` are.

No ranking and no recommendation.
