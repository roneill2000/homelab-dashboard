# Spec: Gaming PC GPU history that records all the time

Ryan, 12 Nov 2026. Builder: pi. Reviewer: Claude.

## Problem

The Local AI page's **Gaming PC GPU** panel (`PcGpuPanel`, `src/app.jsx` ~line 315) only has data while the page is open.
The page pings `/api/ai/live` every 2 s; that makes `ai_monitor.pc_gpu_poll()` read `http://192.168.0.12:9835/gpu` and keep
the last 150 samples (~5 min) **in memory only**. Leave the page and nothing is recorded, so a night of local LLM work
leaves no GPU history.

Meanwhile the storage collector (`storage_monitor.collect()`, every ~10 s, always running, independent of viewers) already
fetches the very same `/gpu` JSON for the energy log (`energy_monitor.fetch_pc()`), uses `power_w`, and throws the rest away.

## Goal

Keep the PC GPU readings the collector already has, so the panel shows history for the same ranges as every other chart
(15 m / 1 h / 6 h / 24 h, the shared `ranges` control), whether or not anyone was watching.

## Hard constraints

1. **No new requests to the gaming PC.** Reuse `pc_gpu` from the existing `pc_future` in `storage_monitor.collect()`.
   No new polling loop, no new thread.
2. **Never poll llama-server `/slots` or anything that counts as model activity** outside the existing watched mode.
   (Fast `/slots` polling kept models loaded forever on 27 Oct; that is why live mode exists. Leave `request_live()`,
   `_live_until`, `WATCHDOG_INTERVAL` and the Strata `/metrics` speed logic exactly as they are.)
3. The 2 s live ping (`/api/ai/live`) stays for the big live readings at the top of the panel.
4. Don't touch the energy calculation (`energy_monitor.collect` / `energy_hourly`).
5. Asleep/off PC: `fetch_pc()` returns `None` or raises → **store nothing** for that tick (the chart shows a gap), never
   zeros.

## Backend

Put the code in `backend/gpu_monitor.py` next to the Unraid A310 code (same pattern, and `scripts/deploy.py` already ships
that module; a new module would need deploy.py plumbing: avoid).

- Table: `pc_gpu_samples (ts INTEGER PRIMARY KEY, power_w REAL, temperature_c REAL, utilization_pct REAL,
  memory_util_pct REAL, vram_used_mib REAL, clock_mhz REAL, fan_pct REAL)` created in `initialize()`
  (`CREATE TABLE IF NOT EXISTS`).
- `PC_METRICS` tuple of those 7 column names. Keep `vram_total_mib` and `power_limit_w` only in an in-memory `_pc_latest`
  (they are constants; the chart uses them as max values).
- `pc_collect(connect, value, now)`: if `value` is a dict without `error`, validate each metric with `numeric()` (from
  `activity_monitor`), reject `_pct` > 100 and `power_w` outside 0–1000 (store `None` for a bad field, keep the row);
  `INSERT OR REPLACE` the row at `now`; delete rows older than 86400 s (same 24 h retention as `gpu_samples`). If `value`
  is not usable, write nothing.
- `pc_snapshot(connect, window, bucket, aggregate)`: same shape as `snapshot()`:
  `dict(latest=..., history=..., history_since=..., bucket_seconds=bucket)`, history through the passed
  `aggregate_history` with `metrics=PC_METRICS` and key `'pc'`.
- `storage_monitor.collect()`: after `pc_gpu=pc_future.result()`, call `gpu_monitor.pc_collect(connect, pc_gpu, now)`
  in its own `try/except` that prints `'PC GPU collector:', type(exc).__name__` (a failure must not stop the energy log).
- `storage_monitor` snapshot dict (line ~205): add `pc_gpu=gpu_monitor.pc_snapshot(connect, window, bucket,
  aggregate_history)`, wrapped so an exception gives `pc_gpu=None` instead of failing `/api/storage`.
- Check `/api/storage` stays fast (it is served from `cached_snapshot`; one extra indexed range query per warm).

Size: ~8,640 rows/day max (10 s), a few hundred KB. No change to retention elsewhere.

## Frontend (`PcGpuPanel`)

- New props: `history={storage?.pc_gpu}` and `ranges={ranges}` (pass both in the `view==='ai'` block, line ~971).
- Section heading gets `{ranges}` like `GpuPanel`.
- The four metric buttons (Power / Temperature / GPU load / VRAM) stay. The **chart** draws `history.history` (server
  data, selected range) with `gapSeconds={Math.max(30,(history?.bucket_seconds||10)*2.5)}` so sleep periods show as gaps.
- The **readings** at the top keep using `live.gpu.latest` (2 s). If live is unavailable but `history.latest` is fresh
  (≤ 30 s), use that.
- Optional, only if simple: append `live.gpu.history` points newer than the last server point so the 15 m view moves
  every 2 s while watching. Skip it if it complicates the chart.
- Empty state text: "No GPU history yet. Readings are recorded every 10 seconds while the gaming PC is awake."
  (replace "The chart fills in while this page is open.").
- Metric details note: "~10 s samples, recorded all the time · 24 h history · gaps = PC asleep or unreachable.
  Short bursts between samples can be missed." plus `History starts …` like `GpuPanel`.
- Update the comment at line ~295 (no longer "read only while this page is open").

## Tests and checks

- `tests/test_gpu.py` (or a new `tests/test_pc_gpu.py`): row stored from a good payload; nothing stored for `None`,
  an `{error: ...}` payload or a non-dict; bad fields → `None` while good ones stay; `_pct` > 100 rejected; rows older than
  24 h pruned; `pc_snapshot` shape and that it returns gaps rather than zeros.
- Test that `storage_monitor.collect()` still runs the energy collector when `pc_collect` raises (monkeypatch).
- `npm run build` → `npm run check` (must print `ALL CHECKS OK`) → `npm test`. Browser checks run against the preview,
  which proxies `/api/*` to live: the panel must render with and without `pc_gpu` present.
- Preview screenshots at 412 px dark and 1440 px light of the Local AI page (mock rows are fine).

## Deploy and verify

1. Commit (no AI credit in messages), `git pull --rebase n100 main`, push to n100 (not GitHub).
2. `python3 scripts/deploy.py` (it backs up, patches, health-checks, rolls back on failure).
3. Wait 2 minutes without the page open, then on LXC 104:
   `sqlite3 -readonly /var/lib/homelab-dashboard/metrics.sqlite3 "select count(*), max(ts) from pc_gpu_samples"`:
   rows keep growing ~6/min while the PC is awake.
4. Confirm no new traffic to the PC beyond the existing 10 s `/gpu` read, and that llama-server models still go idle on
   their own (`sleep-idle-seconds`); i.e. nothing new calls `/slots`.
5. Open Local AI → the chart already has the last hours; the range buttons change it.
6. Append one line to `/mnt/share/hermes-changes.md` and report: commits, test counts, what you verified, what you didn't.

## Out of scope

- Longer than 24 h history (the daily energy chart covers long-term use).
- Measuring the PC's CPU/board power (the energy log's flat 55 W estimate stays; a smart plug is the proposed fix).
- Any change to the Unraid A310 panel, the broker, or the AI request history.

---

# Part 2: measured CPU power for the gaming PC (added 12 Nov, Ryan)

## Why

The energy log counts the gaming PC as *GPU (measured) + 55 W flat estimate* for CPU, board and drives
(`energy_monitor.DEFAULTS['pc']['rest_awake_w']`). Measured 12 Nov 13:57 with Windows' RAPL counters during one Strata
Flash reply: **CPU package 11.5 W idle, ~85 W average (peak 102 W) while Flash writes**, CPU ~80 % busy; GPU ~125 W.
So Flash hours are undercounted by ~70 W and idle hours slightly overcounted. Windows exposes the CPU's own meter as
performance counters, no extra software needed:
`\Energy Meter(RAPL_Package0_PKG)\Energy` (cumulative) and `\Energy Meter(RAPL_Package0_PKG)\Power`.
`RAPL_Package0_DRAM` exists but reads 0 on this board: ignore it.

## Hard constraint: near-zero overhead on the gaming PC (Ryan, explicit)

- **No background thread, no timer, no polling loop** on the PC. Read the counter only inside an incoming `/gpu`
  request, under the existing 2 s cache (`CACHE_SECONDS`): at most one read per 2 s however many callers.
- **No subprocess** (no PowerShell, `typeperf`, `Get-Counter`, WMI). Use the PDH API through `ctypes` (`pdh.dll`:
  `PdhOpenQueryW` + `PdhAddEnglishCounterW` once at start-up, then one `PdhCollectQueryData` +
  `PdhGetFormattedCounterValue`/`PdhGetRawCounterValue` per refresh), the same way the script already uses `nvml.dll`.
  Standard library only, as today.
- If PDH fails to open or the counter is missing: omit the fields, log nothing per request, never break `/gpu`.
- **Prove it:** record `pythonw` (the "Homelab GPU metrics" task, runs as SYSTEM) CPU seconds and working set over
  10 minutes before and after the change at the dashboard's normal 10 s polling (`Get-Process -Id <pid>` at start and
  end). Target: added CPU < 0.05 % of one core averaged, added memory < 5 MB. Report the numbers; if it misses the
  target, stop and report instead of shipping.

## PC side (`pc/gpu_metrics.py`)

- Add two fields to the `/gpu` JSON:
  - `cpu_energy_j`: the cumulative package energy counter converted to joules (verify the raw unit against the `Power`
    counter: two reads 10 s apart must agree with the average `Power` within ~5 %);
  - `cpu_pkg_w`: package watts averaged since the previous refresh (convenience for the live panel; null on the first
    read).
- Update the docstring (it still says "only asks while its Local AI page is open"; after Part 1 the collector reads it
  every 10 s).
- Deploy on the PC: back up `gpu_metrics.py` (`.bak-YYYYMMDD-cpu`), copy, restart the scheduled task, check
  `curl http://192.168.0.12:9835/gpu` from LXC 104 (the firewall allows .2 only). Use `ssh proxmox "pcps <<'EOF' … EOF"`.
  Never kill python processes by name on the PC (Strata runs python too): stop only the task's own PID.

## Dashboard side

- `energy_monitor`: keep the previous `cpu_energy_j` and its time; per 10 s collector tick compute
  `cpu_w = Δjoules / Δseconds` when both reads are fresh, Δ > 0 and 0 ≤ cpu_w ≤ 300 (a reset, wrap, sleep/resume gap or
  bad value → no CPU reading this tick, fall back to today's estimate). Prefer this diff over the exporter's `cpu_pkg_w`
  so live-page polling never shifts the averaging window.
- With a CPU reading, the PC device becomes *GPU (measured) + CPU (measured) + `board_rest_w` (estimate)*, with
  `board_rest_w` default **43 W** (= old 55 W minus the measured 11.5 W idle CPU, so idle totals stay about where they
  are) in `DEFAULTS` and overridable in `energy.json`. `measured_w` includes the CPU watts. Parts list:
  `GPU` (measured), `CPU` (measured), `Board, RAM, fans, SSDs` (estimate). Without a CPU reading: exactly today's
  behaviour.
- "How this is worked out" text: add the PC CPU to the measured list.
- Part 1 table `pc_gpu_samples`: add column `cpu_w REAL` (from the same diff) and a **CPU power** metric button in the
  panel (unit W). `CREATE TABLE IF NOT EXISTS` won't add a column to an existing table: use an `ALTER TABLE … ADD
  COLUMN` guarded by a `PRAGMA table_info` check (or ship Parts 1 and 2 together so the table is created with it).

## Tests

- Energy: CPU diff used when valid; reset/negative/huge/stale → fallback to estimate; parts and `measured_w` correct
  both ways; idle example (GPU 19 W + CPU 11.5 W + 43 W) ≈ old estimate (GPU 19 W + 55 W).
- Exporter: unit-test the conversion and "first read → `cpu_pkg_w` null" with the PDH calls stubbed (the test suite runs
  on Linux; keep the ctypes loading behind a function that the test replaces).
- Live check after deploy: one short Flash request; the dashboard's PC watts rise by roughly the CPU increase
  (~+70 W) instead of only the GPU's.
