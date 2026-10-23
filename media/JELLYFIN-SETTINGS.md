# Jellyfin settings (Unraid Docker `Jellyfin`, 192.168.0.17:8096)

Config lives in `/mnt/user/appdata/Jellyfin`. Changed 22 Oct 2026 (backup in `config/backup-20261022/`):

| Setting | Value | Why |
| --- | --- | --- |
| Hardware acceleration | QSV on `/dev/dri/renderD129` (Arc A310) | already set; iGPU renderD128 unused |
| Transcode throttling | **on** | only transcode a few minutes ahead; frees GPU and the data disk (the 5-stream limit) |
| Hardware decoding | h264, hevc, av1, vp9, vc1, **mpeg2video** | MPEG-2 (DVD rips) now on the A310; tested |
| HEVC RExt 10-bit decoding | **on** | A310 supports 4:2:2 10-bit |
| Trickplay (seek thumbnails) | **on**, A310 decoding, not during library scans | scheduled task daily **10:00** (was 03:00, which collides with the A310 nightly review) |
| Real-time folder monitoring | **off** (Movies, Shows) | doesn't work on shfs/rclone mounts; library scan runs **hourly** instead |
| Unraid disk spin-down | **Never** (parity + disk1) | removes the 8-12 s cold start |

Custom task schedules: `config/ScheduledTasks/64f5f44c-…bbcc.js` (trickplay 10:00), `7738148f-…3aed.js` (scan hourly).
Capacity: 5 simultaneous 4K HDR remux → 1080p streams (see `evals/a310-transcode-2026-10-22/RESULTS.md`).
Not port-forwarded; reach it remotely through WireGuard. HTTPS name: `https://jellyfin.lan` (old `jellyfin.lan:8096` still works).
