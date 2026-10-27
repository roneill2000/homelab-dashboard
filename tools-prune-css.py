"""One-off cleanup (27 Oct 2026): drop CSS selectors that can never match. A selector is dead when it names a class the
app no longer renders (not in src/*.jsx, allowing for class names built at runtime from a prefix), or when it targets
quick actions / PC power inside the Overview hero (they moved out of the hero on 27 Oct). Rules left with no selectors
are removed; @media blocks left empty are removed. Verified with style-snapshot.mjs (computed styles before/after)."""
import re, sys

FILES = ['src/styles.css', 'src/m3-legacy.css', 'src/m3-components.css', 'src/m3-expressive-components.css', 'src/m3-controls.css', 'src/m3-motion.css']
SRC = open('src/app.jsx').read() + open('src/m3.jsx').read()
DYNAMIC = ('m3-chip--', 'm3-card--', 'm3-btn--', 'service-state--', 'chart-dlg-')
MOVED = re.compile(r'\.ov-hero(--attention)?\s+\.(quick-actions|pc-power)\b')

def dead(selector):
    if MOVED.search(selector): return True
    for c in re.findall(r'\.([a-zA-Z][\w-]*)', selector):
        if c not in SRC and not c.startswith(DYNAMIC): return True
    return False

def split_selectors(s):
    parts, depth, cur = [], 0, ''
    for ch in s:
        depth += ch in '(['; depth -= ch in ')]'
        if ch == ',' and depth == 0: parts.append(cur); cur = ''
        else: cur += ch
    parts.append(cur); return parts

def prune(css):
    """Walk top-level items, keeping comments and formatting; recurse into @media/@supports."""
    out, i, n, dropped = [], 0, len(css), 0
    while i < n:
        if css.startswith('/*', i):
            j = css.index('*/', i) + 2; out.append(css[i:j]); i = j; continue
        if css[i].isspace(): out.append(css[i]); i += 1; continue
        j = css.find('{', i)
        if j < 0: out.append(css[i:]); break
        prelude = css[i:j]; depth, k = 1, j + 1
        while depth:
            if css[k] == '{': depth += 1
            elif css[k] == '}': depth -= 1
            k += 1
        body = css[j + 1:k - 1]; p = prelude.strip()
        if p.startswith('@media') or p.startswith('@supports'):
            inner, d = prune(body); dropped += d
            if re.sub(r'/\*.*?\*/|\s', '', inner, flags=re.S): out.append(prelude + '{' + inner + '}')
        elif p.startswith('@'):
            out.append(css[i:k])
        else:
            sels = split_selectors(prelude); keep = [s for s in sels if not dead(s)]
            dropped += len(sels) - len(keep)
            if keep: out.append(','.join(keep).lstrip('\n') if keep != sels else prelude); out.append('{' + body + '}')
        i = k
    return ''.join(out), dropped

total = 0
for f in FILES:
    before = open(f).read(); after, d = prune(before); total += d
    if '--dry' not in sys.argv and after != before: open(f, 'w').write(after)
    print(f'{f}: {d} dead selectors, {len(before) - len(after)} bytes removed')
print('total dead selectors:', total)
