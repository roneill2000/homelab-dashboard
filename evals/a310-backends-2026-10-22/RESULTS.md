# Arc A310 backend tests, 22 October 2026

Model Qwen3.5-4B (unsloth GGUF, revision e87f176), one A310 (4 GB, i915, GuC 70.53, no Resizable BAR) on Unraid.
Same prompts, 6,144-token window (Ask Qwen's setting), thinking off. Raw rows: `matrix-run*.jsonl`.

## Winner: SYCL built ahead of time for the A310

| Setup | Load | Write, short | Read, 2.6K prompt | Write, 2.6K prompt |
| --- | --- | --- | --- | --- |
| **SYCL, A310 build, Q4_K_M** | 21 s first, 4–9 s after | **18.0 tok/s** | **263–285 tok/s** | **15.3 tok/s** |
| SYCL, official image (JIT) | 224 s every load | 17.8 | — | — |
| Vulkan, Q4_K_M (Ask Qwen today) | 11–13 s | 12.6 (16.4 at 4K) | 162 | 11.9 |
| SYCL A310 build, IQ4_XS | 15 s | 13.6 | 179 | 12.0 |
| Vulkan, IQ4_XS | 27 s | 8.7 | 138 | 8.4 |
| Vulkan, UD-IQ3_XXS | 25 s | 6.6 | 63 | 6.7 |

## Speculative decoding: no gain on this card

| Mode | Result |
| --- | --- |
| 0.8B draft model (Qwen3.5-0.8B Q4_K_M, rev 6ab4614) | Crashes on first request: `UR_RESULT_ERROR_OUT_OF_HOST_MEMORY` in Level Zero |
| Built-in MTP (`draft-mtp`) | Not possible: this GGUF has no MTP layers |
| Prompt n-grams (`ngram-mod`) | Loads, same speed as without it (18.0 / 15.3) |

## Notes
- Smaller i-quants are slower on Arc: unpacking them costs more GPU time than the smaller size saves.
- The official SYCL image compiles GPU kernels at every container start (224 s). Building `libggml-sycl.so`
  with `-DGGML_SYCL_DEVICE_ARCH=acm-g11` removes that. `build_aot.sh` rebuilds it on Unraid
  (Intel deep-learning-essentials image + `intel-ocloc` 26.18.38308.1, ~20 min) and layers it onto the
  pinned official image as `homelab/llama-sycl-a310:b11804`. The image is local to Unraid; rerun the script
  after updating llama.cpp.
- Vulkan lost speed going from a 4K to a 6K window (16.4 → 12.6 tok/s); SYCL did not.
