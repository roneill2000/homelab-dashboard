"""One-off refactor (26 Oct 2026): move every rule that styles buttons, connected button groups and chips out of the
stylesheets into src/m3-controls.css (loaded last), keeping their relative order, then drop declarations that a later
rule with the identical selector (same @media context) always overrides. Verified with style-snapshot.mjs."""
import re, sys

FILES = ['src/styles.css', 'src/m3-legacy.css', 'src/m3-components.css', 'src/m3-expressive-components.css']
CONTROL = re.compile(r'\.(m3-btn|primary-button|secondary-button|text-button|segments|m3-segment|m3-segmented|m3-chip|issue-chip|quick-actions|live-chip)\b')

def blocks(css):
    """Top-level items: ('comment', text) | ('rule', selector, body) | ('at', prelude, body)."""
    out, i, n = [], 0, len(css)
    while i < n:
        if css[i].isspace(): i += 1; continue
        if css.startswith('/*', i):
            j = css.index('*/', i) + 2; out.append(('comment', css[i:j], None)); i = j; continue
        j = css.find('{', i)
        if j < 0: break
        sel = css[i:j].strip(); depth, k = 1, j + 1
        while depth:
            if css[k] == '{': depth += 1
            elif css[k] == '}': depth -= 1
            k += 1
        out.append(('at' if sel.startswith('@') else 'rule', sel, css[j + 1:k - 1].strip())); i = k
    return out

def split_selectors(s):
    parts, depth, cur = [], 0, ''
    for c in s:
        depth += c == '('; depth -= c == ')'
        if c == ',' and depth == 0: parts.append(cur.strip()); cur = ''
        else: cur += c
    parts.append(cur.strip()); return [p for p in parts if p]

def decls(body):
    out, depth, cur = [], 0, ''
    for c in body:
        depth += c == '('; depth -= c == ')'
        if c == ';' and depth == 0: out.append(cur.strip()); cur = ''
        else: cur += c
    if cur.strip(): out.append(cur.strip())
    return [d for d in out if d and ':' in d]

moved = []          # (media or None, selector, [decls]) in cascade order
kept_text = {}
for f in FILES:
    src = open(f).read(); keep = []
    for kind, a, b in blocks(src):
        if kind == 'comment': keep.append(a); continue
        if kind == 'at' and a.startswith('@media'):
            inner_keep = []
            for k2, s2, b2 in blocks(b):
                if k2 != 'rule': inner_keep.append(s2 if k2 == 'comment' else f'{s2}{{{b2}}}'); continue
                sels = split_selectors(s2); ctl = [x for x in sels if CONTROL.search(x)]; rest = [x for x in sels if not CONTROL.search(x)]
                if ctl: moved.append((a, ','.join(ctl), decls(b2)))
                if rest: inner_keep.append(f"  {','.join(rest)}{{{b2}}}")
            if [x for x in inner_keep if not x.startswith('/*')]: keep.append(a + '{\n' + '\n'.join(inner_keep) + '\n}')
            continue
        if kind == 'at': keep.append(f'{a}{{{b}}}'); continue
        sels = split_selectors(a); ctl = [x for x in sels if CONTROL.search(x)]; rest = [x for x in sels if not CONTROL.search(x)]
        if ctl: moved.append((None, ','.join(ctl), decls(b)))
        if rest: keep.append(f"{','.join(rest)}{{{b}}}")
    kept_text[f] = keep

# Drop a declaration when a LATER moved rule with the identical (media, selector) sets the same property with the same
# importance (or a later !important beats a normal one). Same selector => same elements and specificity, so it never wins.
def prop(d): return d.split(':', 1)[0].strip().lower()
def important(d): return d.rstrip().endswith('!important')
cleaned, dropped = [], 0
for i, (m, s, ds) in enumerate(moved):
    later = [x for x in moved[i + 1:] if x[0] == m and x[1] == s]
    keep = []
    for d in ds:
        beaten = any(prop(d) == prop(d2) and (important(d2) or not important(d)) for _, _, ds2 in later for d2 in ds2)
        if beaten: dropped += 1
        else: keep.append(d)
    if keep: cleaned.append((m, s, keep))

if '--dry' in sys.argv:
    print(f'rules moved: {len(moved)}, declarations dropped as always-overridden: {dropped}, rules left: {len(cleaned)}'); sys.exit()

for f, keep in kept_text.items():
    head = ''
    open(f, 'w').write('\n'.join(keep) + '\n')
lines = ['/* Buttons, connected button groups (.segments / .m3-segment) and chips: the single home for their styles.',
         '   Consolidated 26 Oct 2026 from styles.css, m3-legacy.css, m3-components.css and m3-expressive-components.css',
         '   (tools-consolidate-controls.py); computed styles verified identical with style-snapshot.mjs.',
         '   Loaded last. Order matters: later rules refine earlier ones (variants, states, phone layout). */']
cur = None
for m, s, ds in cleaned:
    if m != cur:
        if cur: lines.append('}')
        if m: lines.append(m + '{')
        cur = m
    lines.append(('  ' if m else '') + f"{s}{{{';'.join(ds)}}}")
if cur: lines.append('}')
open('src/m3-controls.css', 'w').write('\n'.join(lines) + '\n')
print(f'rules moved: {len(moved)}, declarations dropped: {dropped}, rules in m3-controls.css: {len(cleaned)}')
