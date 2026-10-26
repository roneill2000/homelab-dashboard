# dashboard-theming Specification

## ADDED Requirements

### Requirement: Generated colour roles
The dashboard SHALL take every Material 3 colour role from `m3-tokens.mjs`, which generates light and dark schemes at build time from seed `#7de5c0` with `@material/material-color-utilities` 0.3.0 (`SchemeTonalSpot`, contrast level 0) and emits them as `--md-sys-color-*` custom properties.

#### Scenario: Build emits both schemes
- **WHEN** `npm run build` runs
- **THEN** `index.html` contains a `/* GENERATED: m3-tokens */` block with `:root` (dark) and `:root[data-theme=light]` values for every role, including `surface`, `surface-container-*`, `primary`, `on-primary`, `secondary-container`, `outline`, `outline-variant`, `error` and `scrim`

#### Scenario: Seed change regenerates everything
- **WHEN** `SEED` in `m3-tokens.mjs` changes and the page is rebuilt
- **THEN** all colour roles in both themes change without editing any other file

### Requirement: Custom accent colours
Service and chart accents MUST use exactly nine harmonised custom colours (`teal`, `green`, `cyan`, `blue`, `indigo`, `purple`, `pink`, `orange`, `amber`), each exposed as `--md-custom-<name>`, `--md-custom-on-<name>`, `--md-custom-<name>-container` and `--md-custom-on-<name>-container`.

#### Scenario: Service card accent
- **WHEN** a service from the catalog is rendered
- **THEN** its accent is one of the nine names (field `accent`) and its colour resolves through `var(--md-custom-<name>)`

#### Scenario: Element opts into an accent
- **WHEN** an element has `data-accent="<name>"`
- **THEN** `--accent`, `--on-accent`, `--accent-container` and `--on-accent-container` resolve to that custom colour's roles

### Requirement: No colour literals outside generated tokens
Source files SHALL contain no hex colour literal: `src/app.jsx` and `src/styles.css` reference colours only through `var(--md-sys-color-*)`, `var(--md-custom-*)` or `color-mix()` of those.

#### Scenario: Grep finds no hex
- **WHEN** `grep -cE '#[0-9a-fA-F]{3,8}\b' src/app.jsx src/styles.css` runs on the finished branch
- **THEN** both counts are 0

#### Scenario: Bridge removed
- **WHEN** the change is complete
- **THEN** `src/m3-bridge.css` does not exist, `build.mjs` does not load it, and no rule in `src/styles.css` uses `--bg`, `--sidebar`, `--panel`, `--panel2`, `--hover`, `--border`, `--text`, `--muted`, `--faint`, `--accent`, `--accent-soft`, `--shadow`, `--font` or `--heading`

### Requirement: Theme choice and default
The theme MUST be stored under localStorage key `homelab.theme.v2`, applied as `data-theme` on the root element without reload, and default on first visit to the operating system preference (`prefers-color-scheme`).

#### Scenario: First visit follows the OS
- **WHEN** no `homelab.theme.v2` value exists and the OS prefers light
- **THEN** the page renders with `data-theme="light"`

#### Scenario: Toggle persists
- **WHEN** the theme toggle in the top app bar is pressed and the page is reloaded
- **THEN** the chosen theme is still applied

### Requirement: Readable contrast
Body text on its surface MUST keep a contrast ratio of at least 4.5:1 in both themes.

#### Scenario: Accessibility check
- **WHEN** `accessibility-check.mjs` measures contrast in dark and light
- **THEN** both ratios are at least 4.5
