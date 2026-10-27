# Homelab dashboard: Codex audit of what's new (27 Oct 2026, afternoon)

**Task:** Independent audit of everything that changed after the fresh UX review (`cebbd0c`, reviewed build `fa3cf3b`).
**Workstream:** homelab-dashboard. **Audience:** Ryan.
**Earlier:** `2026-10-27-homelab-expressive-layout.md`; fresh review at `~/homelab-fresh-review/audits/2026-10-27-fresh-ux-review.md`.

## Working state
- Repository `~/homelab`, n100 `main` at `cd9d1fb`; live deploy matches (backup `20261027T151758190891Z`).
- GitHub `main` is at `cc962b8`: it has the demo GIF commits but not the three commits below. Ryan pushes to GitHub himself, or asks for it explicitly.
- Gate: `npm run check > .preview/gate.txt` and require `grep -q "^ALL CHECKS OK"`. Passed on `cd9d1fb`.
- In progress in another session: README motion clips (new recorder under `showcase/`). **Do not edit README.md or showcase/ while that is open.**

## What changed (audit these)
1. **Motion on 120 Hz phones (`0258011`).** Ryan's Pixel 11 Pro XL dropped frames in the first ~0.4 s after taps.
   - Hero status icon: pop-in stays on `.ov-hero__glyph`, the endless turn moved to its `::before` shape. Before, two transform animations on one element pushed both onto the main thread.
   - `::view-transition-group(page){animation:none}`: the default group animation tweened height on the main thread on every page change.
   - Spring `linear()` curves are now sampled by error (RDP, 0.001 tolerance) instead of every Nth point. Max error went from 0.43% to 0.14%.
   - Verified with a Chrome trace (`.preview/composite-probe.mjs <url>`): no composite failures for the glyph or page group. The remaining main-thread animations are the page-title width morph and button border-radius morph.
2. **Fresh-review fixes (`9e0fb0b`, `cd9d1fb`).** These cover all six findings. The optional pinned-tile resize is not done; it needs Ryan's choice.
   - Storage capacity mode: headline shows Used % and Free, and live I/O drops to a secondary line (`StorageCard`, `src/app.jsx`).
   - `NightlySummary` shows "Incomplete coverage…" when `evidence.coverage_limited` is set (neutral tone).
   - Overview health tiles: `.ov-hero` is an inline-size container; at ≤700px of container width the grid becomes 2 columns.
   - Drive health `.drive-row` is a fixed grid (icon / details / temp / badge). On phones and at ≥1800px, temperature and badge sit under the details.
   - Phone `main a:not([class])` is `inline-flex`, so external-link icons stay on the label's line.
   - Disk summary rows show "History →" (arrow only on phones). Manual Docker update rows link to `http://192.168.0.4/Docker`. The Network chart has a Send/Receive legend (new `legend` prop on `Chart`).
3. **Demo media from another agent (`d831d31`..`cc962b8`).** Adds `record-demo.mjs`, `record-animations.mjs`, `showcase/demo.gif` (10 MB, 800×500, 20 fps, 41 s), `showcase/demo.mp4` and `.mailmap`. These are already on public GitHub.

## Asks
1. Check each fresh-review finding against its acceptance criteria at 412, 768, 1024 and 1440, in light and dark. Include neighbouring widths for the tile threshold (600–900px).
2. Check that the motion changes did not change the look: hero glyph in normal and attention states, page change at phone and desktop sizes. Re-run the composite trace on live.
3. **Privacy review of public files.** `record-animations.mjs` masks the WAN address with a regex that contains its first two octets, so a partial WAN IP is public. Confirm this, list any other public file that exposes WAN, ntfy topic, tokens or personal device names, and propose the fix. Do not print the values in the audit. History rewrites are Ryan's to run.
4. Presentation check of the README media: readable text, length, file size, whether it shows motion, phone gestures and both themes. Review only; another session is replacing the clips.
5. Anything else that regressed or still looks unfinished.

## Rules
- Repository is public. Write the audit to `private/audits/2026-10-27-whats-new-audit.md` (git-ignored), or to an unmerged branch in a separate worktree. No app edits, merges, pushes or deploys.
- Block every non-GET request in the browser; no service actions, AI queries or update runs.
- Never print secrets, the WAN address, the ntfy topic or tokens.
- No Co-Authored-By or any tool credit in commits.

## Files that matter
- `src/m3-motion.css` (end of file), `m3-expressive.mjs`, `src/m3-controls.css` (block headed "Fresh UX review, 27 Oct 2026"), `src/app.jsx`.
- `.preview/fps-probe.mjs`, `.preview/composite-probe.mjs`: frame-time and compositor probes (ignored files, local only).

## Unverified
- 120 Hz smoothness on the real phone after `0258011`: Ryan has not reported back yet.
- Drive rows with the long SMART detail line were not in the preview data (fleet SMART is absent locally). Check them on live.
