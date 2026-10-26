# Design

## Context

- `src/app.jsx` is bundled by `build.mjs` (esbuild) into one `index.html`; `deploy.py` copies it to the dashboard container. React is a global (`/static/react.js`); `src/m3.jsx` is imported by `src/app.jsx`.
- CSS bundle order (in `build.mjs`): generated colour roles (`m3TokensCss()`), `src/m3-sys.css`, `src/m3-bridge.css`, `src/styles.css`, `src/m3-legacy.css`, `src/m3-components.css`. Later files win ties.
- `src/styles.css` is one rule per line (reformatted and verified rule-for-rule identical in Chrome), so whole-line edits are safe.
- 9 views: Overview, All services, Local AI, Media & downloads, Network & DNS, Hosts & containers, Storage, Infrastructure, Updates (the `navigation` array in `App`).
- Playwright checks: 14 scripts. 9 passed on the unchanged dashboard (26 Oct 2026) and form the gate (`npm run check`). The other 5 (ai, coverage, gpu, log, log-groups) already failed then because they expect old live data; they are not part of this change.

## Decisions

### D1. Colours are generated, never hand-picked
`m3-tokens.mjs` runs `SchemeTonalSpot(seed #7de5c0, dark|light, contrast 0)` and emits every `MaterialDynamicColors` role as `--md-sys-color-*`, plus `customColor(..., blend:true)` for the 9 accents as `--md-custom-<name>`, `--md-custom-on-<name>`, `--md-custom-<name>-container`, `--md-custom-on-<name>-container`. Dark is `:root`; light is `:root[data-theme=light]`. The 28 previous accent hexes were grouped by HCT hue (all checked against the nearest base hue). Changing the brand colour means changing `SEED` only.

### D2. Token values come from Google's files
`src/m3-sys.css` and `reference/component-tokens.md` were generated from material-web `tokens/versions/v0_192` (commit 60c0cfa58ad1). State layers: hover 0.08, focus 0.12, pressed 0.12, dragged 0.16. Elevation shadows use material-web's two-shadow formula. Do not "correct" these from memory or from other sites.

### D3. Navigation is rendered per window class
`useWindowClass()` (in `src/m3.jsx`) returns compact (< 600), medium (< 840), expanded (< 1200) or large. Only the navigation for the current class is in the DOM, because the checks require exactly one `.sidebar`, one `.theme-toggle` and unique button names.

| Class | Navigation | `.sidebar` element |
| --- | --- | --- |
| compact | bottom nav bar (Overview, Services, Media, Local AI, More) + modal drawer | modal drawer (hidden and inert when closed) |
| medium | rail: menu button + first 6 of PRIMARY; menu opens a modal drawer | rail |
| expanded | rail with all 9 | rail |
| large | standard drawer with all 9 | drawer |

Grid columns switch with CSS media queries (instant), and `.m3-app__main` is pinned to the last column. That prevents a one-frame overflow after a resize, before React swaps the navigation (this was a real race, found by `cache-forecast-check`).

Accessible names are kept: rail items use `aria-label` with the full label and count (e.g. "All services 28"); the menu button is "Open navigation"; search is "Find a service"; the search input is "Search service names and IPs"; results are anchors inside `.command-results`.

### D4. Legacy classes first, markup second
`src/m3-legacy.css` makes the legacy classes look like M3 components, so every view is M3 even before markup changes. agy's tasks then add M3 classes next to legacy ones (never remove a legacy class name from JSX: checks and CSS select on them) and delete legacy CSS only when the class is no longer used.

### D5. Icons
`Icon` (in `src/m3.jsx`) renders Material Symbols Rounded; old icon keys map through `SYMBOLS`. Icons have a fixed `1em` box with `overflow:hidden`, so the ligature name never shows as wide text before the font loads (that caused overflow in the checks).

### D6. Theme
Stored key `homelab.theme.v2` unchanged. On a first visit with no stored value, the theme follows `prefers-color-scheme`. The toggle is an icon button in the top app bar (`.theme-toggle`).

## Risks / Trade-offs

- **Seed-generated surfaces are teal-tinted neutrals**, not the old navy. Accepted; change `SEED` to retune.
- **Material Symbols adds a font request.** It is subset with `icon_names`; new icons must be added to `m3-icons.mjs` or they show blank.
- **The 5 non-gate checks still fail** on old live-data expectations. Out of scope; fix separately.

## Migration / Rollback

Nothing is deployed by this change. To ship: Ryan reviews screenshots, merges `m3-redesign`, and runs `deploy.py`. Rollback: redeploy the previous `index.html` from `main`.
