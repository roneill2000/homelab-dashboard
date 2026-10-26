# dashboard-shape-elevation-state Specification

## ADDED Requirements

### Requirement: Shape scale
Corner radii SHALL come from `--md-sys-shape-corner-*` tokens: none 0, extra-small 4px, small 8px, medium 12px, large 16px, extra-large 28px, full 9999px.

#### Scenario: Component shapes
- **WHEN** components render
- **THEN** buttons, segmented buttons and the search bar use full; cards and icon containers use medium; chips use small; text fields, menus, snackbars and tooltips use extra-small; dialogs use extra-large; the modal drawer uses large on its end corners

### Requirement: Elevation levels
Shadows MUST use `--md-sys-elevation-level0` to `level5`, computed from the material-web elevation formula (key shadow 30%, ambient 15% of `--md-sys-color-shadow`).

#### Scenario: Raised surfaces
- **WHEN** a dialog, snackbar or docked search view is open
- **THEN** it uses elevation level 3; the navigation bar and menus use level 2; elevated cards and hovered filled/tonal buttons use level 1

#### Scenario: Flat by default
- **WHEN** outlined cards, panels, the page background or the standard drawer render
- **THEN** they have no shadow (level 0)

### Requirement: State layers
Interactive elements SHALL show a state layer in their content colour at the M3 opacities: hover 0.08, focus 0.12, pressed 0.12, dragged 0.16 (`--md-sys-state-*`).

#### Scenario: Hover and press
- **WHEN** the pointer hovers, then presses, a control with class `m3-state`
- **THEN** an overlay at 8% then 12% opacity appears without changing layout or text colour

#### Scenario: Keyboard focus
- **WHEN** a control receives keyboard focus
- **THEN** a 3px `secondary`-coloured focus outline is visible in both themes

### Requirement: Motion tokens and reduced motion
Transitions MUST use `--md-sys-motion-easing-*` and `--md-sys-motion-duration-*` tokens, and all animation and transition durations SHALL drop to 1ms under `prefers-reduced-motion: reduce`.

#### Scenario: Drawer opens
- **WHEN** the modal drawer opens
- **THEN** it slides in with emphasized-decelerate easing over medium4 (400ms)

#### Scenario: Reduced motion
- **WHEN** the OS requests reduced motion
- **THEN** the drawer, dialog, snackbar and progress animations complete immediately
