# Test plan: Strata (Qwen 3.8 Flash-Next, 68B MoE, IQ2_XS) vs Qwen 3.8 27B (llama.cpp)

Goal: decide, with numbers we trust, which model should be the default for (1) OpenCode coding work, (2) Ask about the homelab, (3) the nightly log review, and (4) general chat in Open WebUI. Written 29 Oct 2026.

## 1. Why the earlier result isn't enough

The Gemini run reported Strata 4/4 correct and the 27B 0/4 (no final answer). That may be true, but the test can't show it:

| Problem with the earlier test | Why it matters | What this plan does instead |
|---|---|---|
| One run per question, four questions | One lucky or unlucky sample decides everything; no error bars | 150+ scored items, 3–5 runs each, confidence intervals |
| Famous puzzles (doctor's son riddle, water jugs, zebra grid, Landau's theorem) | Very likely in both models' training data, so this measures memorisation | Sets published after both models' training cutoff, plus our own new items |
| 27B capped at 1,200–1,800 tokens with `reasoning-effort=medium` | A thinking model that needs 3,000 tokens "fails" at 1,800 because of the cap, not the model | Same generous budget for both (16k), and a separate budget-sensitivity test |
| Settings not matched (sampling, template, system prompt) | Any of these can swing results by 10+ points | One config file, identical for both, recorded in every result |
| The model or agent graded its own answers ("100% correct, production-grade") | Self-grading is unreliable and biased | Automatic graders where possible, blind judging otherwise, human spot checks |
| Speed taken from a few runs | Prefill was reported at 390 tok/s; a first request on 29 Oct ran at 18 tok/s (307 tokens in 17 s) | Warm and cold runs, a context-length sweep, and time-to-answer per task |
| A 2-bit model (IQ2_XS) compared without noting quantisation | Quality loss from 2-bit quantisation shows up most on exact tasks (maths, code, long context) | Quant of each model recorded; include tasks sensitive to it |

## 2. Fixed conditions (both models)

- **Hardware:** RTX 5070 Ti 16 GB, 32 GB RAM, same NVMe. **Only one engine loaded at a time.** Stop llama-server before Strata runs and the other way round, then check `nvidia-smi` shows only the engine under test.
- **Endpoints:** both through their OpenAI-compatible `/v1/chat/completions`: llama-server `:8080` (`qwen3.8-27b`), Strata `:8088` (`swift-1.5-iq2_xs`).
- **Record for each engine:** version, model file and quantisation, context length, KV cache type, MTP or draft settings, GPU layers or expert cache size. 27B quant: write down exactly which file (for example Q4_K_M), since "27B" alone isn't a fair label.
- **Sampling (Qwen's recommended values for thinking mode):** temperature 0.6, top_p 0.95, top_k 20, min_p 0. A second "deterministic" pass at temperature 0 for the exact-answer sets.
- **Thinking:** on for both. `max_tokens` 16,384 for reasoning sets (24k where the context allows), 4,096 for chat and tool tasks. No reasoning-effort setting unless both engines support the same one.
- **Prompts:** same system prompt (empty, or one fixed line), same chat template (the model's own). Strip `<think>…</think>` before grading. Score a missing closing tag as "no answer" and count it separately.
- **Context:** cap both at 32k, Strata's limit, so long-context tests are like for like.

## 3. What to measure

| Category | Metric |
|---|---|
| Correctness | pass@1 (mean over runs) with a 95% confidence interval; pass@k for code |
| Reliability | think-loop rate (no `</think>` within budget), empty-answer rate, format-violation rate |
| Efficiency | reasoning tokens per item, answer tokens per item |
| Speed | time to first token, prefill tok/s, decode tok/s, **total time to a correct answer** (the number that matters day to day) |
| Tools | valid tool-call JSON rate, correct tool and arguments, multi-step task completion |
| Cost | GPU watts × seconds per task (from `pc/gpu_metrics.py`), peak VRAM and RAM |
| Stability | errors, crashes or slowdowns over a 60-minute soak; speed with an NVMe expert-cache miss (cold) vs warm |

## 4. Test sets

Use published sets with automatic grading where possible, and keep only items published after both models' training cutoff (check each model card). Fixed random subsets with a recorded seed, so both models get the same items.

| Set | Items | Tests | Grader |
|---|---|---|---|
| A. Maths: newest AIME/HMMT problems after the cutoff, plus a MATH-500 level-5 subset | 40 | multi-step reasoning, exactness | exact numeric match |
| B. Science and knowledge: GPQA-Diamond subset, MMLU-Pro subset | 60 | breadth, hard multiple choice | letter match |
| C. Code: LiveCodeBench problems dated after the cutoff (easy, medium, hard) | 40 | writing correct programs | hidden unit tests in a sandbox |
| D. Instruction following: IFEval subset | 50 | doing exactly what was asked (length, format, constraints) | IFEval's checker |
| E. Tool calling: BFCL subset (simple, multiple, parallel, multi-turn) | 50 | valid, correct function calls | BFCL's AST checker |
| F. Long context: RULER-style needle and multi-hop at 4k, 8k, 16k, 30k | 40 | using the whole window (where 2-bit quants often slip) | exact match |
| G. Fresh puzzles we write ourselves: logic grids, word problems, number puzzles, never published | 20 | reasoning without memorisation | answer key, written before running |
| H. Homelab tasks (our real use, below) | 30 | what we actually need | rubric plus blind judging |

**H. Homelab tasks.** These matter most for choosing a default.
1. **Ask about the homelab (10):** real questions ("why was the cache full last night?", "which disk is busiest?") using the exact evidence bundle the dashboard sends (`nightly/qwen_context.py`). Graded on: correct against the evidence, no invented facts, cites the evidence.
2. **Nightly log review (5):** five saved real nights of logs. Graded on: findings match the human-checked list, no false alarms, valid output format.
3. **OpenCode agent tasks (10):** run inside OpenCode on a throwaway copy of `~/homelab` (never the live repo or hosts), each with a check that passes or fails. Examples:
   - add a unit test for `download_proxy.MAGNET`
   - fix a planted bug
   - add a field to `/api/widget`
   - refactor a CSS rule without changing screenshots

   Score: task done (tests pass), number of tool calls, wall-clock time, and whether it did anything outside the task.
4. **JSON and format discipline (5):** prompts that require strict JSON or a fixed template. Score the parse rate.

## 5. How runs are done

1. **Harness:** one Python script (`evals/run.py`) runs on the Mac or Proxmox and calls each endpoint with the shared config. For every item it saves the request, the full raw response including reasoning, the timings and the token counts, to `evals/results/<engine>/<set>/<item>-<run>.json`. Nothing is graded during the run.
2. **Runs:** 5 runs per item at temperature 0.6 for sets A, C, G and H (3 for the rest), plus 1 at temperature 0. Order is shuffled per run so caches and warm-up don't favour one model.
3. **Warm-up:** 3 unscored requests after each engine starts. Then one **cold** measurement: restart the engine, drop the OS file cache, and time the first real request, so Strata's NVMe streaming is measured honestly.
4. **Speed sweep:** prompts of 512, 2k, 8k, 16k and 30k tokens with a 512-token answer, 5 times each: prefill tok/s, decode tok/s, TTFT, plus MTP acceptance rate for Strata.
5. **Budget sensitivity:** on set A, rerun both at `max_tokens` 2k, 4k, 8k and 16k. This answers directly whether the 27B "failure" was the cap.
6. **Soak:** 60 minutes of mixed requests per engine, tracking errors, speed drift, VRAM, temperature and watts.

## 6. Grading

- **Automatic first:** exact match, unit tests, IFEval and BFCL checkers. The answer-extraction rules are written before the runs and are the same for both models.
- **Blind judging for open-ended answers (sets G and H):**
  - Responses are stripped of reasoning and anything that identifies the model, and shown to the judge in random A/B order.
  - Judge: Claude (a different model family from both contestants), with a written rubric.
  - Each pair is judged twice with the order swapped; a verdict that flips with the order counts as a tie.
- **Human check:** Ryan grades a random 20% of the judged items blind. If agreement with the judge is under 80%, human grades replace the judge's.
- **Statistics:**
  - Results are paired by item.
  - McNemar's test for accuracy differences on each set.
  - Bootstrap 95% intervals for means and for time to a correct answer.
  - A difference only counts if its interval excludes zero.

## 7. Decision rules (set before looking at results)

Strata becomes the default for a use if, on that use's sets:
- correctness is no worse than the 27B (interval for the difference includes or exceeds zero), **and**
- median time to a correct answer is at least 25% lower, **and**
- think-loop or empty-answer rate is under 2%, **and**
- tool-call validity (OpenCode) is at least 95%, and the 60-minute soak is clean.

If Strata wins on speed but loses on correctness for exact work (maths, code, long context), the likely split is Strata for chat and Ask, and the 27B for OpenCode and the nightly review.

## 8. Effort

| Step | Time |
|---|---|
| Harness, config, item selection, answer keys for set G | ~4 h |
| Homelab task fixtures (saved evidence and logs, OpenCode sandbox and checks) | ~3 h |
| Runs (unattended, both engines, all sets and sweeps) | ~10–14 h of GPU time; overnight |
| Grading, judging, Ryan's 20% blind check | ~2 h, of which ~45 min is Ryan's |
| Write-up with tables and the decision | ~1 h |

## 9. Rules for whoever runs this

- Only one engine on the GPU at a time. Note any run where both were loaded and redo it.
- OpenCode tasks run on a disposable copy of the repo with no access to live hosts or keys.
- Save raw outputs before grading, and never edit them.
- No result is reported without its interval and run count.
