# Homelab M3 Expressive independent audit — 26 October 2026

**Task:** Review current site with independent agents and return actionable design feedback.
**Workstream:** homelab-dashboard. **Audience:** Ryan.
**Earlier:** `2026-10-26-homelab-m3-audit.md`.

## Working state
- Repository: `~/homelab`; branch: `audit/m3-expressive-independent-2026-10-26`.
- Audited application commit: `905a606`; starting working tree clean.
- This branch adds only audit/handoff documentation and updates this workstream's index.
- No implementation, merge, push, deployment, or service action.

## Settled
- Three independent reviews covered Material fidelity, UX composition, and interaction/accessibility; prior audit conclusions were withheld from reviewers.
- Live CSS and application JS hashes matched local build; 54 fresh captures cover nine pages, three widths, both themes.
- All nine `npm run check` scripts passed: `ALL CHECKS OK`.
- Four browser-reproduced issues: enlarged chart Tab loop; responsive overlay inertness; press-induced action-row wrap; background scroll behind modal.
- Design judgment: preserve current Material foundations; improve task hierarchy on Overview/AI and current-state summaries on operational pages.
- Full evidence, source references, proposed changes, and acceptance criteria: `audits/2026-10-26-m3-expressive-independent-review.md`.

## Decisions Ryan made
- Requested audit and actionable feedback, with independent agents; no implementation requested this turn.

## Next step
Implement the audit's interaction corrections with regression coverage, then prepare Overview and Local AI composition changes for visual review if Ryan requests implementation. Keep current colors/navigation and unknown-data semantics.

## Files that matter
- `audits/2026-10-26-m3-expressive-independent-review.md` — durable audit.
- `.preview/shots/independent-review-2026-10-26/` — 54 local ignored screenshots.
- `.preview/check-output.txt` — passing gate output; later runs may replace it.

## Unverified
- Five non-gate legacy checks were not rerun. Operational OAuth/backup items from earlier handoff were not revalidated.
- Motion compositing and connectivity announcements need recorded-motion/screen-reader verification.
