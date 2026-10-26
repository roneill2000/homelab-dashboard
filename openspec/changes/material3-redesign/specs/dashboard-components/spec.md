# dashboard-components Specification

## ADDED Requirements

### Requirement: Shared M3 components
Views SHALL build controls from the components in `src/m3.jsx` (`Button`, `IconButton`, `Segmented`, `Chip`, `BadgeHost`, `Card`, `CardHeader`, `LinearProgress`, `IndeterminateProgress`, `Spinner`, `TextField`, `SelectField`, `Dialog`, `Snackbar`, `ListItem`, `Divider`, `PageHeader`, `Section`) styled by `src/m3-components.css`, with sizes from the material-web v0_192 component tokens.

#### Scenario: Buttons
- **WHEN** an action renders
- **THEN** the main action is a filled button, secondary actions are tonal, low-emphasis actions are text buttons, all 40px high with full shape

#### Scenario: Segmented buttons
- **WHEN** a range or metric selector renders
- **THEN** it is a segmented button group whose selected segment has `aria-pressed="true"`, secondary-container background and a check icon

### Requirement: Legacy classes look like M3 until migrated
Every legacy class still used by a view (`panel`, `panel-title`, `icon-tile`, `section-heading`, `segments`, `primary-button`, `secondary-button`, `text-button`, `notice`, `track`, `service-card`, `issue-chip`) MUST be styled as its M3 equivalent by `src/m3-legacy.css`, and a rule there SHALL be deleted only once the class no longer appears in `src/app.jsx`.

#### Scenario: Unmigrated panel
- **WHEN** a view still renders `className="panel"`
- **THEN** it looks like an outlined card (surface, 1px outline-variant, 12px corners, no shadow)

### Requirement: Dialogs
Confirmations MUST use `Dialog`: surface-container-high, 28px corners, elevation 3, a 32% scrim, focus moved into the dialog on open, focus trapped while open and returned to the trigger on close.

#### Scenario: DNS pause confirmation
- **WHEN** "Pause DNS 5 min" is pressed and then Escape
- **THEN** the dialog opens with Cancel focused, closes on Escape, and focus returns to the trigger

### Requirement: Snackbar
Transient messages MUST use `Snackbar` (inverse-surface, extra-small corners, `role="status"`) positioned above the bottom navigation bar on phones.

#### Scenario: Copy address
- **WHEN** an address is copied
- **THEN** a snackbar "Address copied" is announced and disappears after about 3.6 seconds

### Requirement: Progress indicators
Meters SHALL render as M3 linear progress (4px, surface-container-highest track, primary or `--accent` indicator) and loading states as linear or circular indeterminate indicators.

#### Scenario: Disk usage
- **WHEN** a storage card shows capacity used
- **THEN** a 4px progress track fills to the used percentage in the card's accent

### Requirement: Data and behaviour unchanged
The redesign MUST NOT change data fetching, API calls, actions (wake/sleep PC, DNS pause, updates, Ask AI), links or the `build.mjs` -> `index.html` -> `deploy.py` pipeline.

#### Scenario: Diff review
- **WHEN** the branch diff of `src/app.jsx` is reviewed
- **THEN** changes are limited to markup, class names, component usage and colour references
