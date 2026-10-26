# dashboard-navigation Specification

## ADDED Requirements

### Requirement: Navigation by window size class
The app shell SHALL render navigation from the window class returned by `useWindowClass()`: compact (<600px) bottom navigation bar plus modal drawer; medium (600-839px) navigation rail with the first six primary destinations and a menu button that opens the modal drawer; expanded (840-1199px) navigation rail with all nine destinations; large (>=1200px) standard navigation drawer with all nine.

#### Scenario: Phone
- **WHEN** the viewport is 412px wide
- **THEN** a bottom bar shows Overview, Services, Media, Local AI and More, and More or the "Open navigation" button opens a modal drawer listing all nine views

#### Scenario: Landscape tablet
- **WHEN** the viewport is 998px wide
- **THEN** the rail shows all nine destinations and no menu button

#### Scenario: Desktop
- **WHEN** the viewport is 1440px wide
- **THEN** a 360px standard drawer shows all nine destinations with the services count and updates badge

### Requirement: Stable navigation contracts
Navigation MUST keep the accessible names and classes the checks use: destination buttons named by their full label (services as "All services <count>"), class `nav-item` on rail and drawer items, exactly one `.sidebar` element at any width, and on compact widths the "Open navigation" button as the second tab stop after the skip link.

#### Scenario: Named destination
- **WHEN** a check clicks `getByRole('button',{name:/^All services \d+$/})`
- **THEN** exactly one visible button matches and navigates to All services

#### Scenario: Closed modal drawer
- **WHEN** the compact drawer is closed
- **THEN** it is inert, hidden, has no box shadow, and is skipped by Tab

### Requirement: Modal drawer isolation
While the modal drawer is open the main content MUST be inert, the bottom bar MUST not render, and a 32% scrim SHALL close the drawer on press.

#### Scenario: Open drawer
- **WHEN** the drawer is open on a phone
- **THEN** only one button named "Overview" exists and Escape or the scrim closes it

### Requirement: Top app bar
A 64px small top app bar (class `m3-top-app-bar topbar`) SHALL hold the brand, search, a live/sync chip, "Refresh telemetry" and the theme toggle (class `theme-toggle`, named "Switch to light"/"Switch to dark"), and every button in it MUST be at least 44px in both dimensions.

#### Scenario: Scrolled
- **WHEN** content scrolls under the bar
- **THEN** the bar background changes from surface to surface-container

#### Scenario: Narrow phone
- **WHEN** the viewport is 360px wide
- **THEN** the brand text and chip label hide and the page does not scroll horizontally

### Requirement: Search
Search SHALL open from a search bar named "Find a service" at >=840px or a search icon button with the same name below 840px, or Ctrl/Cmd+K, as a search view (full screen below 840px, docked above) with an input named "Search service names and IPs" and result links under `.command-results`.

#### Scenario: Filter and close
- **WHEN** the user types "sab" then presses Escape
- **THEN** one result link to `https://sab.lan` is shown, and after Escape focus returns to the trigger
