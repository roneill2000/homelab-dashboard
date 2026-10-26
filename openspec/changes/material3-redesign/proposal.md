# Proposal: Material 3 redesign of the Homepage dashboard

## Why

The dashboard looked home-made: about 100 hard-coded colours, a custom sidebar and top bar, and one-off buttons, chips, meters and dialogs. Ryan wants it to look and behave like a Material 3 product (https://m3.material.io/): colour roles generated from tonal palettes, the M3 type, shape, elevation, state-layer and motion systems, M3 components, and adaptive navigation.

## What Changes

Done on branch `m3-redesign` by Claude (do not redo; see tasks.md section 0):
- **Colour:** light and dark schemes generated at build time from seed `#7de5c0` (`m3-tokens.mjs`, Google's `@material/material-color-utilities` 0.3.0). The 54 inline accent colours in `src/app.jsx` became 9 harmonised M3 custom colours (`--md-custom-teal|green|cyan|blue|indigo|purple|pink|orange|amber`).
- **System tokens:** type scale, shape, elevation, state layers and motion in `src/m3-sys.css`, generated from Google's material-web token files (v0_192, commit 60c0cfa58ad1).
- **Icons:** Material Symbols Rounded, subset to the names in `m3-icons.mjs`.
- **Shell:** adaptive navigation (navigation drawer ≥ 1200 px, navigation rail 600-1199 px, bottom navigation bar < 600 px), small top app bar, search bar and search view, M3 dialog and snackbar.
- **Components:** `src/m3.jsx` (React) and `src/m3-components.css`.
- **Legacy classes** (`.panel`, `.primary-button`, `.segments` and others) restyled as M3 components in `src/m3-legacy.css`, so every view already looks M3.
- **Checks:** `npm run check` runs the 9 Playwright scripts that passed before any change. `node screenshots.mjs <label>` captures every view in both themes at 412/768/1440 px.

Left for agy (tasks.md sections 1-7): mechanical clean-up. Remove the colour bridge, replace the remaining hex colours in `src/styles.css` from a lookup table, put M3 classes on legacy buttons and cards, apply metric typography, delete dead CSS, then take screenshots for Ryan's review.

Not changing: views, data, API calls, actions, the stored theme key `homelab.theme.v2`, and the `build.mjs` → `index.html` → `deploy.py` pipeline. Deploying is Ryan's call.

## Capabilities

### New Capabilities
- `dashboard-theming`: generated colour roles, custom colours, theme persistence.
- `dashboard-typography-icons`: M3 type scale and Material Symbols.
- `dashboard-shape-elevation-state`: shape, elevation, state layers, motion.
- `dashboard-navigation`: adaptive navigation, top app bar, search.
- `dashboard-components`: M3 components replacing custom controls.
- `dashboard-verification`: check gate, screenshots, no-overflow contract.

### Modified Capabilities
(none: the repo had no specs)

## Impact

- New files: `m3-tokens.mjs`, `m3-icons.mjs`, `run-checks.mjs`, `screenshots.mjs`, `src/m3.jsx`, `src/m3-sys.css`, `src/m3-components.css`, `src/m3-legacy.css`, `src/m3-bridge.css` (temporary).
- Changed: `src/app.jsx` (shell, icons, colours), `src/styles.css` (one rule per line, shell rules removed), `build.mjs` (token generation, font links), `package.json` (dev dependency, scripts), `ui-smoke.mjs` and `responsive-check.mjs` (no hard-coded service count or SAB address).
- Google Fonts now also serves Material Symbols Rounded (subset).
- Public repo: these files hold no addresses, credentials or tokens beyond what the repo already contains.
