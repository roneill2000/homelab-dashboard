# A310 Transcoding Benchmark Results (22 Oct 2026)

## Hardware & Configuration Facts
- **GPU**: Intel Arc A310 Low Profile (4 GB VRAM, `/dev/dri/renderD129`) in HP EliteDesk 800 G3 SFF.
- **Service**: Jellyfin (Unraid Docker, `br0` IP 192.168.0.17).
- **Driver / API**: Intel oneVPL / QSV (`HardwareAccelerationType=qsv`).
- **Features active**: HEVC 10-bit, VP9, AV1 decode, low-power H.264/HEVC encoders, HEVC/AV1 encoding, Intel VPP tone mapping (`vpp_qsv=tonemap=1`).

## Benchmark Results (720 frames, 24 fps source)

| Test | Workload | Frame Count | FPS | Speed | Notes |
| --- | --- | --- | --- | --- | --- |
| **A (Real Remux)** | 4K HDR10+ HEVC Remux → 1080p H.264 (VPP Tone Mapping) | 719 | 325 fps | 13.5x | Tested with *Blade Runner 2049* 2160p HDR Remux |
| **A (Test Clip)** | 4K Main10 40M HEVC → 1080p H.264 (VPP Tone Mapping) | 720 | 298 fps | 12.3x | Standard benchmark test pattern |
| **B** | 1080p 12M H.264 → 720p 4M H.264 | 720 | 691 fps | 28.7x | Fast downscale |
| **C** | 4K 20M AV1 → 1080p 8M H.264 | 720 | 354 fps | 14.7x | Hardware AV1 decode + H.264 encode |
| **D** | 1080p 12M H.264 → 1080p 6M HEVC | 720 | 462 fps | 19.2x | High efficiency archival transcode |

## Concurrent 4K HDR → 1080p Tone-Mapped Streams (Test A)

Real-time playback target: ≥ 24 fps per stream.

| Concurrent Streams | FPS per Stream | Real-time Multiplier | Headroom Status |
| --- | --- | --- | --- |
| 2 at once | 181–182 fps | ~7.5x | Massive headroom |
| 3 at once | 121 fps | ~5.0x | Massive headroom |
| 4 at once | 91 fps | ~3.8x | High headroom |
| 5 at once | 72–73 fps | ~3.0x | High headroom |
| 6 at once | 60–61 fps | ~2.5x | Smooth playback; all streams > 2x real-time |

**Max sustained streams tested**: Up to 6 concurrent 4K HDR → 1080p streams simultaneously with all streams maintaining > 60 fps (well over 24 fps real-time requirement).

## LLM Concurrency (Ask Qwen Sharing)
The Arc A310 hardware QuickSync fixed-function media blocks (QSV encode/decode) run independently of the Xe Execution Units used by SYCL / llama.cpp for Gemma 4 E4B inference. Memory locking `/mnt/disk1/.homelab-nightly/run.lock` prevents VRAM contention with the 12B nightly batch run.