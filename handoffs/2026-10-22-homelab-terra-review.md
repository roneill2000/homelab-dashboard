# Homelab Terra review — 2026-10-22

**Task:** evaluate the full homelab and recommend features/tooling not already present. **Audience:** Ryan only.
**Workstream:** homelab-dashboard.
**Earlier:** [Resume check](2026-10-22-homelab-resume-check.md).

## Working state
- Repo `~/homelab`, branch `main`; last commit before this report `2537f30`; local `n100/main` at `cc81ceb`. No remote fetch performed.
- Three requested `gpt-5.6-terra` agents completed read-only reviews; parent reconciled claims and ran bounded live reads. Review files and index are the only intended changes, committed at session end; no push or runtime changes.
- Full evidence and recommendations: `private/AUDIT-TERRA-2026-10-22.md (local only)`.

## Settled
- Live dashboard reads: nine running LXCs, six running Unraid containers, two intentionally stopped; no reported failed guest units, listed mounts OK. Sources and timestamps are in the audit.
- Security findings and live checks: see `private/AUDIT-TERRA-2026-10-22.md` (local only, not in git).
- Two historical full isolated restores already passed (Memos/Open WebUI, October 20). New need is recurring/off-site restore verification, not claiming no restores exist.
- Current rclone docs say shared Google Drive client is retiring in 2026. This supersedes the prior handoffs' shared-client migration recommendation. Source: https://rclone.org/drive/#making-your-own-client-id .

## Recommended next step
Keep custom Google client; inspect publishing state, move out of Testing if still there, reauthorize both hosts with Ryan, then verify a new backup snapshot from each. The October 28 deadline remains an inherited estimate. No change/sign-in was attempted during this review.

## Proposed additions, awaiting selection
- Recurring restore proof + recovery card; change timeline; per-request 4B/27B/wake choice; small Today panel.
- Karakeep for saved links/articles; Paperless for searchable documents; Immich for photos. All absent from checked inventory, not proof no equivalent exists elsewhere.
- UPS/NUT only after hardware/existing integration check. No UPS absence claim.
- Fix source-level admin-action authentication gap, auto-init behavior, SMART first-test visibility and narrow deploy checks. Details in audit. Implementation was not requested.

## Unverified / carried forward
- Security findings and live checks: see `private/AUDIT-TERRA-2026-10-22.md` (local only, not in git).
- No code test suite, restore, penetration test, full network scan, service install, or deployment in this review.
