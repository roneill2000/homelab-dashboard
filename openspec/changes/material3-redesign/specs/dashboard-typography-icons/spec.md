# dashboard-typography-icons Specification

## ADDED Requirements

### Requirement: Material 3 type scale tokens
Text SHALL be styled with the M3 type scale (`--md-sys-typescale-{display,headline,title,body,label}-{large,medium,small}-{font,size,line-height,weight,tracking}`) defined in `src/m3-sys.css` from material-web tokens v0_192 (commit 60c0cfa58ad1), with the brand typeface Manrope and the plain typeface DM Sans.

#### Scenario: Page title
- **WHEN** a view is shown
- **THEN** its `<h1>` uses headline-medium (1.75rem / 2.25rem) in the brand typeface

#### Scenario: Section and card titles
- **WHEN** a section heading or card title renders
- **THEN** section headings use title-large and card titles use title-medium

#### Scenario: Labels
- **WHEN** a button, chip or segmented button renders
- **THEN** its text uses label-large (0.875rem, weight 500)

### Requirement: Fonts load from one place
DM Sans, Manrope and Material Symbols Rounded MUST be loaded by `<link>` elements written by `build.mjs` in the document head, and no stylesheet may use `@import` for fonts.

#### Scenario: Built head
- **WHEN** `index.html` is built
- **THEN** its `<head>` has one Google Fonts link for DM Sans + Manrope and one for Material Symbols Rounded, and `src/*.css` contain no `@import`

### Requirement: Material Symbols icons
Icons SHALL render as Material Symbols Rounded through the `Icon` component in `src/m3.jsx`, and the font request MUST include only the names listed in `m3-icons.mjs` (sorted, via `icon_names=`).

#### Scenario: Legacy icon key
- **WHEN** code renders `<Icon name="disk"/>`
- **THEN** the `SYMBOLS` map turns it into the `hard_drive` symbol

#### Scenario: New icon name
- **WHEN** markup uses a symbol name not in `m3-icons.mjs`
- **THEN** the name is added to `ICONS` in `m3-icons.mjs` before the build is accepted

### Requirement: Icons never change layout while loading
Every `.material-symbols-rounded` element MUST occupy a fixed 1em by 1em box with hidden overflow, so the ligature text shown before the font loads cannot widen the layout.

#### Scenario: Cold font cache
- **WHEN** the page loads before the icon font arrives
- **THEN** no element overflows horizontally and the overflow assertions in `cache-forecast-check.mjs` pass on repeated runs

### Requirement: Icons are decorative to assistive tech
Icon elements MUST carry `aria-hidden="true"`, and every icon-only control MUST have an accessible name from `aria-label`.

#### Scenario: Icon button name
- **WHEN** an `IconButton` with `label="Refresh telemetry"` renders
- **THEN** its accessible name is exactly "Refresh telemetry" and the ligature text is not part of it
