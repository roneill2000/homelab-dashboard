# Material 3 Expressive independent review — 26 October 2026

The site has credible Material 3 foundations. Expressive hierarchy is uneven across pages, and several interactions need correction. Refine the current system; retain its palette, navigation, typography, and working components.

## Scope and evidence

- Reviewed implementation: `905a606`, clean working tree before this documentation branch. The supplied handoff described an older commit.
- Live CSS and application JavaScript hashes matched the local `index.html`; the initial telemetry script differed as expected.
- Three independent agents reviewed Material fidelity, visual/product UX, and interaction/accessibility. They were instructed to avoid prior audit conclusions and formed findings separately. This reduces anchoring; it does not establish objective neutrality for design judgments.
- Captured all nine views at 412×915, 768×1024, and 1440×900, in both themes: 54 screenshots under `.preview/shots/independent-review-2026-10-26/`. Additional Overview captures: `.preview/shots/independent-review-desktop/`.
- Live browser audit requests blocked non-GET/HEAD methods. No backend actions or deployments were performed.
- `npm run check`: all nine gate scripts passed, ending `ALL CHECKS OK`. Output: `.preview/check-output.txt`. The five additional legacy scripts were not rerun; their present status is unverified.
- Screenshots and temporary audit scripts remain local in ignored `.preview/`. Source references below refer to the reviewed commit.

## What to preserve

Generated color roles and coherent light/dark themes; Google Sans/Roboto typography; rounded symbols; adaptive rail/bar navigation; selected connected-button shapes; state layers; spring curves and reduced-motion handling. The health surface, loaded-model emphasis, and grouped service lists are useful foundations. Keep explicit unknown-data states and benchmark provenance.

Google describes Expressive design as purposeful use of color, shape, size, motion, and containment to direct attention. It also emphasizes familiar interactions and functionality. That supports improving hierarchy and behavior here; it does not mandate extra saturation or decorative shapes. [Google Design research](https://design.google/library/expressive-material-design-google-research). Scale, spacing, and variable type are additional tools for highlighting meaningful moments. [Material design evolution](https://design.google/library/material-design-eras).

## First: correct reproduced interaction defects

| Priority | Finding and evidence | Change | Acceptance |
| --- | --- | --- | --- |
| P1 | Enlarged chart keyboard focus loops on Close. Dialog's focusable selector excludes the chart SVG with `tabindex`. `src/m3.jsx:146`, `src/m3.jsx:149`, `src/app.jsx:33`. | Use a complete, visibility-aware tabbable implementation, including explicit tabindex. Choose appropriate initial focus for chart dialogs. | Tab/Shift+Tab reaches chart and Close, arrows/Home/End inspect samples, focus remains inside, Escape restores the opener. |
| P1 | Background navigation loses inertness across responsive changes. Search open at 1440→448 leaves the new bottom nav `inert=false`; drawer open at 448→700 leaves the new rail `inert=false`. Inert effects omit window class. `src/app.jsx:359`. | Centralize modal ownership; apply background inertness to newly mounted responsive regions. | Cross 599/600, 839/840, 1199/1200 in both directions with every overlay open. Background controls remain unavailable; dismissal focuses a visible trigger. |
| P2 | Holding Pause DNS at 780×1100 grows quick-actions height 56→112px and moves Check updates y=380→442. Pressed padding adds 20px. No action was executed. `src/m3-controls.css:136`. | Reserve space or redistribute widths within a stable group; avoid wrap triggered by pressing. | No row-count or hero-height change when any action is pressed/released at 760–840px or other breakpoints. |
| P2 | Wheeling over chart/search scrims moves the underlying document from scrollY 0→500 at 1440×800. `src/m3.jsx:144`, `src/app.jsx:477`. | Share scroll locking between overlays; preserve document position and permit internal scrolling. | Wheel/touch/keyboard outside modal cannot scroll background; dialog scroll works; close restores starting position. |
| P2 | Local AI's Ask on Homepage navigates to Overview and scrolls to the top, while its composer is below favorites/backups. Source-confirmed mismatch: `src/app.jsx:213`, `src/app.jsx:364`, `src/app.jsx:453`, `src/app.jsx:465`. | Open/focus the composer directly, or place the homelab assistant in Local AI. | Activating Ask lands on the intended visible input with focus, without another search or scroll. |

The button correction preserves the intent of Expressive press behavior. [Official Material button guidance](https://github.com/material-components/material-components-android/blob/master/docs/components/CommonButton.md).

## Next: design changes with the largest visible effect

These are design judgments, not claims of specification violations. Targets below are proposed product acceptance criteria.

### 1. Compose Overview around health and useful launches

The healthy hero currently contains the health headline, an update chip, PC power, DNS pause, update check, and four metrics. It receives substantial emphasis even when all is clear. On tablet, its height pushes useful launches down. See `412-dark-overview.png`, `768-light-overview.png`, `1440-light-overview.png`; `src/app.jsx:438`.

Use a compact health/exception summary. Keep the expressive container and large status headline, with a clear action when attention is required. Place pinned launches and the backup/review summary next. Move routine PC/DNS/update operations into a separate quick-actions row. Keep metric details accessible without giving every number equal emphasis.

Proposed order: health and exceptions → pinned services plus backup/review summary → quick actions → optional assistant and Activity.

Accept when the first 412×915 and 768×1024 viewport contains health plus at least two useful pinned launches. Preserve unmonitored/unknown context when compressing the hero. Check healthy, attention, and stale states separately.

### 2. Make Local AI a place to start a task

Current model cards make speed/context prominent, repeat Open chat, and push the homelab assistant below model metadata. The loaded-model feature is useful but stretches widely on desktop. See `412-light-local-ai.png`, `1440-dark-local-ai.png`; `src/app.jsx:189`.

Lead with two tasks: Chat with a model and Ask about the homelab. Feature the loaded model with one prominent Open chat action. Keep model identity and capabilities nearby; put benchmark speed/context in supporting text. Show other models as compact rows or a chooser, with GPU monitoring below. Avoid duplicating the same WebUI launcher at equal prominence.

Accept when either task can begin from the first phone viewport, Ask focuses its input, and model identity plus recorded/live benchmark distinctions remain available.

### 3. Establish surface and content hierarchy on operational pages

Storage shows four similarly emphasized chart panels before drive health. Media begins with a configuration paragraph and then a dense metrics surface. Broad CSS gives generic panels the same highest container fill (`src/m3-expressive-components.css:204`), which limits differentiation. See `1440-dark-storage.png`, `412-dark-storage.png`, `412-dark-media-downloads.png`; `src/app.jsx:467`, `src/app.jsx:469`.

Give each page a concise current-condition summary and one obvious relevant next action. Keep detail on quieter surfaces. On phone Storage, show compact disk rows before selected/expanded history. Preserve the chart grid where desktop comparison is useful. On Media, lead with streams/downloads/queue and service actions; put configuration prose under Playback setup.

Accept when all four disks and explicit health exceptions can be scanned before opening detail, and Media's first viewport answers whether anything is playing/downloading. Idle, unavailable, and zero-activity states must remain distinct. Theme-role selection should express these relationships, not add arbitrary colors. [Material color system](https://m3.material.io/styles/color/the-color-system).

### 4. Give Infrastructure a navigable structure

The captured page is 5,911px tall on phone and 4,180px on desktop. Services precede nightly review, logs, backups, recovery evidence, change history, and another log summary. Source order: `src/app.jsx:470`, `src/app.jsx:471`.

Lead with current recovery/log/review status. Provide Recovery / Logs & review / Changes navigation or anchored sections; demote service launchers. Consider a clearer destination name such as Recovery & logs alongside Hosts & containers.

Accept when backup status, latest review, and changes are each one navigation action from the page top. Preserve evidence and avoid forcing phone users through unrelated timelines.

### 5. Make exceptional service states legible without hover

Service rows use small status dots, with explanations in title/ARIA labels. Sighted touch users need a visible distinction for Unreachable, Stale, and Unmonitored. Keep healthy indicators quiet; add words for exceptions and unknown states. See All services captures in both themes.

Accept when a screenshot alone distinguishes those three states without crowding names or pin controls. This finding does not claim screen-reader labeling is absent.

## Polish after the page hierarchy is settled

- **Motion:** page and card entrance effects can overlap; spatial curves also drive opacity. Separate opacity effects from geometry and select a coherent page entrance or descendant stagger. This is a source-level concern, not a demonstrated perceptual failure; record normal-motion navigation before deciding its severity. `src/m3-expressive-components.css:19`, `:153`, `:159`. [Official motion guidance](https://m3.material.io/styles/motion/overview/how-it-works).
- **Desktop association:** constrain service-list reading width or align explicit columns; keep model identity/action close together. Wide rows currently separate name/purpose from trailing detail and actions.
- **Connectivity announcements:** a persistent polite region should announce interrupted/recovered states once. Current warning/chip lacks consistent live-region semantics (`src/app.jsx:430`, `:436`). This is code inference; verify with a screen reader. Routine telemetry updates should remain quiet.
- **Native selects:** a styled exposed dropdown may improve consistency, but replacing native keyboard/touch behavior is lower priority than the defects above. Native select use alone does not invalidate a Material web implementation.

## Verification for the next implementation

The existing gate provides useful functional coverage but does not approve visual fidelity. The accessibility test directly focuses the inline chart, bypassing the broken enlarged-dialog tab order. The responsive test omits 600–839px and does not resize while overlays are open. Screenshot generation does not compare reviewed reference images. Reduced-motion captures cannot establish normal-motion quality.

1. Add regression cases for the four reproduced overlay/press defects before fixing them. Keep the existing nine-script gate.
2. Create deterministic visual fixtures for healthy, attention, stale, unavailable, empty, loading, and active states. Review both themes at 412, 768, and 1440px; test breakpoint edges and wide-screen overflow separately.
3. Record normal and reduced-motion transitions. Review selected, hover, pressed, focus, and disabled states. Confirm that live updates preserve user context.
4. Review Overview and Local AI compositions first. Apply the accepted hierarchy to Storage, Media, and Infrastructure afterward.

No website implementation changed in this audit. No merge, push, or deployment was performed. Operational handoff items, including OAuth, backups, and stale legacy checks, were outside this design audit and were not revalidated.
