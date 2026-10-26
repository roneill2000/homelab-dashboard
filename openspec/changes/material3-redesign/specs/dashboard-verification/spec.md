# dashboard-verification Specification

## ADDED Requirements

### Requirement: Check gate
Every task MUST end with `npm run check`, which starts `preview.py` on 127.0.0.1:8799 and runs the nine gate scripts (accessibility, activity, cache-forecast, content, nightly, qwen, responsive, ui-smoke, updates), and the task is complete only when it prints `ALL CHECKS OK`.

#### Scenario: Failing gate
- **WHEN** any gate script prints FAIL, exits non-zero or times out
- **THEN** the task is not complete; the change is fixed or reverted, and the check scripts' assertions are not weakened

#### Scenario: Stale-data scripts
- **WHEN** `npm run check:all` runs ai, coverage, gpu, log or log-groups checks
- **THEN** their failures are compared against the 26 Oct 2026 baseline (they already failed on stale live data) and are not a gate

### Requirement: No horizontal scroll
The page MUST not scroll horizontally at 360, 390, 412, 448, 998, 1440, 1920, 2560 or 3440px wide, in either theme.

#### Scenario: Responsive check
- **WHEN** `responsive-check.mjs` visits Overview, Storage, All services and Hosts & containers at each width
- **THEN** `document.documentElement.scrollWidth` never exceeds the viewport width and no card overflows

### Requirement: Visual review
Each task that changes markup or CSS SHALL produce screenshots with `node screenshots.mjs <label>` (412, 768 and 1440px, dark and light, all nine views) and the run MUST report no JS errors.

#### Scenario: Before and after
- **WHEN** a view task finishes
- **THEN** `.preview/shots/<label>/` contains that view in six variants and the script output has no "JS ERROR" or "NAV FAIL" line

### Requirement: Build integrity
`npm run build` MUST succeed and `index.html` SHALL contain the generated token block, both font links and the bundled app.

#### Scenario: Clean build
- **WHEN** `npm run build` runs
- **THEN** it prints `Built index.html` and exits 0

### Requirement: Deploy only after approval
The branch MUST NOT be deployed with `deploy.py` or merged into main until Ryan has reviewed the screenshots and approved.

#### Scenario: Finished branch
- **WHEN** all tasks pass the gate
- **THEN** the work stops at a committed branch with screenshots, awaiting Ryan's approval
