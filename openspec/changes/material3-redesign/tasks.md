# Tasks: Material 3 redesign (executor: agy)

## Read first

1. `openspec/changes/material3-redesign/proposal.md` and `design.md`
2. Every file in `openspec/changes/material3-redesign/reference/` (`hex-to-token.md` is used in section 2)
3. The header comments at the top of `src/m3.jsx`, `src/m3-components.css` and `src/m3-legacy.css`

## Rules (apply to every task)

- Work in the homelab repo on branch `m3-redesign` (Ryan pushes it to the share repo `/mnt/share/repos/homelab-dashboard`; use your usual clone of that repo, run `git fetch` there, then `git checkout m3-redesign`).
- One-time setup before task 1: `npm install`, then `npx playwright install chromium-headless-shell`. Google Chrome and `python3` must be installed (the checks use both). Then run `npm run check` once on the untouched branch: it must end with `ALL CHECKS OK`. If it does not, STOP and tell Ryan (the problem is the environment, not your work).
- `npm run check` retries a failed script once. A line ending `(passed on retry)` counts as OK.
- One task = one commit. Commit message = the task's title. **No `Co-Authored-By` or any other AI credit line.**
- After every task, run `npm run build` and then `npm run check`. The last line must be exactly `ALL CHECKS OK`.
  If it is not: run `git checkout -- .`, STOP, and report the `BAD` lines to Ryan. Do not try to fix the failure yourself.
- Never edit `*-check.mjs`, `ui-smoke.mjs` or `run-checks.mjs` to make a check pass.
- Never run `deploy.py`. Ryan decides when to deploy.
- Use exact string replacement (find text, replace text). Do not reformat, reorder or "tidy" anything else.
- In a replacement table, a row that matches 0 times in a file is fine: skip it. Where a task gives an expected count, the count must match exactly, otherwise STOP and ask Ryan. Each task's Verify step is what decides pass or fail.

## 0. Already done (do not redo)

- [x] 0.1 `ec31a55` checks: `run-checks.mjs` runner; `npm run check` = 9-script gate
- [x] 0.2 `79a2e20` foundation: generated M3 colour roles, M3 system tokens, component CSS, Material Symbols, legacy bridge
- [x] 0.3 `0773d4c` `src/styles.css` reformatted to one rule per line; fonts loaded via `<link>`
- [x] 0.4 `c531ff3` app shell: navigation drawer/rail/bar, top app bar, search view, M3 dialog and snackbar; `src/m3.jsx`
- [x] 0.5 `6d9eada` all 54 inline accent hexes in `src/app.jsx` replaced with M3 custom colours
- [x] 0.6 `aa3d6f3` `src/m3-legacy.css`: legacy classes styled as M3 components

## 1. Remove the bridge

- [ ] 1.1 In `src/styles.css`, replace every occurrence of each find string, **in this order** (top to bottom):

  | Find | Replace |
  | --- | --- |
  | `var(--accent-soft)` | `var(--md-sys-color-secondary-container)` |
  | `var(--accent)` | `var(--md-sys-color-primary)` |
  | `var(--panel2)` | `var(--md-sys-color-surface-container)` |
  | `var(--panel)` | `var(--md-sys-color-surface-container-low)` |
  | `var(--sidebar)` | `var(--md-sys-color-surface-container-low)` |
  | `var(--bg)` | `var(--md-sys-color-surface)` |
  | `var(--hover)` | `var(--md-sys-color-surface-container-high)` |
  | `var(--border)` | `var(--md-sys-color-outline-variant)` |
  | `var(--text)` | `var(--md-sys-color-on-surface)` |
  | `var(--muted)` | `var(--md-sys-color-on-surface-variant)` |
  | `var(--faint)` | `var(--md-sys-color-on-surface-variant)` |
  | `var(--shadow)` | `var(--md-sys-elevation-level1)` |
  | `var(--font)` | `var(--md-ref-typeface-plain)` |
  | `var(--heading)` | `var(--md-ref-typeface-brand)` |

- [ ] 1.2 Apply the same table, in the same order, to `src/app.jsx` and `src/m3-legacy.css`.
- [ ] 1.3 Delete the file `src/m3-bridge.css`. In `build.mjs`, find `'src/m3-bridge.css', ` (with the trailing comma and space) and replace it with nothing.
- [ ] 1.4 Verify. This command must print nothing:
  `grep -nE "var\(--(bg|sidebar|panel|panel2|hover|border|text|muted|faint|accent|accent-soft|shadow|font|heading)\)" src/*.css src/app.jsx`
  If it prints anything, STOP and ask Ryan. Then build and check (Rules), and commit "m3: remove the legacy colour bridge".

## 2. Hex colours in styles.css

- [ ] 2.1 Open `openspec/changes/material3-redesign/reference/hex-to-token.md`. It lists every hex colour left in `src/styles.css` and its replacement.
- [ ] 2.2 Sort the rows by the length of the Find string, longest first (8-digit hexes before 6-digit before 4-digit before 3-digit).
- [ ] 2.3 In `src/styles.css`, replace every occurrence of each Find string (case-insensitive) with its Replace string, in that order.
- [ ] 2.4 Verify: `grep -cE "#[0-9a-fA-F]{3,8}\b" src/styles.css` must print `0`. If not, STOP and ask Ryan with the remaining lines (`grep -nE "#[0-9a-fA-F]{3,8}\b" src/styles.css`).
- [ ] 2.5 Build and check (Rules), commit "m3: styles.css colours from M3 tokens".

## 3. Buttons to M3 classes in JSX

- [ ] 3.1 Record the counts before: run
  `grep -o 'className="primary-button"' src/app.jsx | wc -l; grep -o 'className="secondary-button"' src/app.jsx | wc -l; grep -o 'className="text-button"' src/app.jsx | wc -l`
  and write the three numbers down.
- [ ] 3.2 In `src/app.jsx`, replace every occurrence:

  | Find (exact) | Replace |
  | --- | --- |
  | `className="primary-button"` | `className="m3-btn m3-btn--filled m3-state primary-button"` |
  | `className="secondary-button"` | `className="m3-btn m3-btn--tonal m3-state secondary-button"` |
  | `className="text-button"` | `className="m3-btn m3-btn--text m3-state text-button"` |

  Do **not** change `className="text-button inline"` or any className that is not exactly one of the three find strings.
- [ ] 3.3 Verify: the three commands from 3.1 now each print `0`, and
  `grep -o 'm3-btn--filled m3-state primary-button' src/app.jsx | wc -l` (and the same for `m3-btn--tonal m3-state secondary-button` and `m3-btn--text m3-state text-button`) print the three numbers you wrote down.
- [ ] 3.4 Build and check (Rules), commit "m3: buttons use M3 button classes".

## 4. Cards

- [ ] 4.1 List the panel classNames: `grep -o 'className=[^>]\{0,40\}panel[^>]\{0,20\}' src/app.jsx | sort | uniq -c`
- [ ] 4.2 In `src/app.jsx`, replace every occurrence:

  | Find (exact) | Replace |
  | --- | --- |
  | `className="panel"` | `className="m3-card m3-card--outlined panel"` |
  | `className="panel ` | `className="m3-card m3-card--outlined panel ` |
  | `className={'panel ` | `className={'m3-card m3-card--outlined panel ` |
  | `className={"panel ` | `className={"m3-card m3-card--outlined panel ` |

  The 2nd to 4th find strings end with a space. Do not touch classNames where `panel` is not the first word (for example `qwen-panel`, `gpu-panel`).
- [ ] 4.3 Verify: rerun the command from 4.1. Every line that started with `className="panel`, `className={'panel` or `className={"panel` now starts with `m3-card m3-card--outlined panel`. `git diff --stat` shows only `src/app.jsx`.
- [ ] 4.4 Build and check (Rules), commit "m3: panels are M3 outlined cards".

## 5. Typography for big numbers

- [ ] 5.1 Append exactly this block to the end of `src/m3-legacy.css`:

```css
/* Big metric numbers -> display-small (headline-medium on phones) */
.summary-stat strong,.net-now strong,.storage-values strong,.storage-grid .storage-values strong{
  font-family:var(--md-sys-typescale-display-small-font);font-size:var(--md-sys-typescale-display-small-size);
  font-weight:var(--md-sys-typescale-display-small-weight);line-height:var(--md-sys-typescale-display-small-line-height);
  letter-spacing:var(--md-sys-typescale-display-small-tracking);font-variant-numeric:tabular-nums}
@media (max-width:599px){.summary-stat strong,.net-now strong,.storage-values strong,.storage-grid .storage-values strong{
  font-family:var(--md-sys-typescale-headline-medium-font);font-size:var(--md-sys-typescale-headline-medium-size);
  line-height:var(--md-sys-typescale-headline-medium-line-height)}}
```

- [ ] 5.2 Build and check (Rules), commit "m3: metric numbers use the display-small type style". Do not change `.mono`.

## 6. Remove dead legacy CSS
These classes are no longer used in `src/app.jsx`: `confirm-dialog`, `download-value`, `icon-button`, `local-ai-hosts`, `sleep-confirm`, `version-label`.
- [ ] 6.1 Confirm: `grep -cE "confirm-dialog|download-value|icon-button|local-ai-hosts|sleep-confirm|version-label" src/app.jsx` prints `0`. If not, STOP and ask Ryan.
- [ ] 6.2 In `src/styles.css`, delete every whole line that starts (after any leading spaces) with one of these exact texts. Expected: **17 lines** deleted in total.
  `.version-label{` · `.icon-button{` · `.icon-button:hover{` · `.download-value{` · `.download-value span{` · `.confirm-dialog{` · `.confirm-dialog h2{` · `.confirm-dialog p{` · `.confirm-dialog>div{` · `.confirm-dialog button{` · `.local-ai-hosts{` · `.sleep-confirm{` · `.sleep-confirm button{`
  (Some of these appear 2 or 3 times, inside and outside `@media` blocks; delete all of them. Do not delete the `@media` lines or `}` lines.)
- [ ] 6.3 In `src/styles.css`, make these exact replacements:
  | Find (exact) | Replace | Expected count |
  | --- | --- | --- |
  | `,.download-value em{` | `{` | 2 |
  | `  .local-ai-hosts,.ai-model-grid{` | `  .ai-model-grid{` | 1 |
- [ ] 6.4 Verify: `grep -cE "\.(confirm-dialog|download-value|icon-button|local-ai-hosts|sleep-confirm|version-label)\b" src/styles.css` prints `0`.
- [ ] 6.5 Build and check (Rules), commit "m3: remove CSS for classes no longer used".

## 7. Screenshots for Ryan

- [ ] 7.1 Run `python3 preview.py &` and wait 5 seconds.
- [ ] 7.2 Run `node screenshots.mjs final`. It must end with `Screenshots in .preview/shots/final` and print no `JS ERROR` or `NAV FAIL` lines. If it does, STOP and report them.
- [ ] 7.3 Stop the preview: `pkill -f "python3 preview.py"`.
- [ ] 7.4 Tell Ryan: "M3 redesign tasks 1-6 done on branch m3-redesign. Screenshots: .preview/shots/final (phone 412px, tablet 768px, desktop 1440px; dark and light; all 9 views)." STOP and wait for his review.

## 8. After Ryan approves (only on his explicit OK)

- [ ] 8.1 Run `/opsx-archive material3-redesign` to archive the change and fold its specs into `openspec/specs/`. Commit "openspec: archive material3-redesign".
- [ ] 8.2 STOP. Merging to `main` and deploying are Ryan's job.
