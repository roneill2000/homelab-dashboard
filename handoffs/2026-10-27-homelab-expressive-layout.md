# Homelab dashboard: Expressive + layout pass (27 Oct 2026)

Live and on n100 `main` at `fac1143` (last app deploy `7bcd088`, backup 20261027T052714840961Z). Gate is 10 suites (`overlay-check` added); deploy only when `npm run check` output contains `ALL CHECKS OK` (grep the saved output, never the exit of a grep).

Done today, all deployed:
- Codex independent audit (`audits/2026-10-26-m3-expressive-independent-review.md`): 4 overlay bugs fixed with regression tests; Overview, Local AI, Storage/Media, Infrastructure, status labels.
- Codex layout review (`~/homelab-ux-review/audits/2026-10-27-live-layout-review.md`): all 8 rows plus Media queue link.
- Expressive: shapes (m3-shapes.mjs), morphing loading indicator, motion layer (src/m3-motion.css), shared-element transitions, tap-length press feedback, category icon shapes, validated chart palette (--md-chart-1..5), phone More sheet with swipe, pull to refresh with exit.
- Colour: darker hero with off-white text; light-theme neutrals less tinted (Ryan disliked the mint).
- Brand corner shows logo + live status (Ryan's choice).
- 124 dead CSS selectors removed (tools-prune-css.py, verified with style-snapshot.mjs).
- Showcase PDF + README rebuilt (`node showcase/build-showcase.mjs`), WAN masked. Ryan pushes to GitHub himself.
- Gaming PC model notes: pc/LLM-SERVICE.md, uncensored profile 51.4 tok/s, share handoff line 74.

Open:
- Google OAuth publish + `rclone config reconnect gdrive:` on Proxmox and Unraid before ~28 Oct (Ryan).
- styles.css / m3-legacy.css still exist (dead rules gone; a full merge into component files is the remaining cleanup).
- Kasm in backup coverage; 5 stale non-gate checks; archive OpenSpec change.
