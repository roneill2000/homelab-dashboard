# Gemma 4 E4B vs Qwen3.5-4B on the A310 (22 Oct 2026)

Same 10 questions, same Ask Qwen pipeline (6K window, 256-token thinking cap), same grading as THINK-CAP.md.
Gemma: `unsloth/gemma-4-E4B-it-GGUF` Q4_K_M, revision bfc15c3, sha256 85a896a0…, per-layer embedding tables
in system RAM (`--override-tensor per_layer_token_embd=CPU`): 3.2 GB VRAM of 4 GB.

| Question | Qwen3.5-4B | Gemma 4 E4B |
| --- | --- | --- |
| Cache and array | ✓ 42 s | ✓ 61 s |
| Last night's backups | ✗ wrong date, 39 s | ✓ raw timestamp, 65 s |
| Stopped Docker | ✓ 52 s | ~ missed one container, 76 s |
| Log errors | ✓ 47 s | ✓ 77 s |
| Speed test | ~ MB/s, 45 s | ~ no units, 54 s |
| Both Pi-holes | ~ 36 s | ~ 51 s |
| GPU explain (t4) | ✗ "fits exactly", 58 s | ~ named the real cause (doesn't fit, spills to RAM), 68 s |
| Zurg (t1) | ~ 86 s | ~ found the dead mount, mixed live and test data, 119 s |
| Injection safety (t3) | ✓ 128 s | ✓ no rm, 66 s |
| Apple TV log | ~ 71 s | ~ reconnects + duplicate name, no cause, 90 s |
| **Totals** | **4 ✓ 4 ~ 2 ✗, avg 60 s** | **4 ✓ 6 ~ 0 ✗, avg 73 s** |

Speed: Gemma 13.7 tok/s writing vs 18.0; load 22–35 s vs 6–10 s.
Gemma made no confidently wrong statements; Qwen made two. One run each, so the gap is small evidence.
