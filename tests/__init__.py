# Tests import the backend modules (backend/) and maintenance scripts (scripts/) by their plain names, as the
# dashboard host runs them from one flat directory. Run from the repo root: npm test
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
for sub in ('backend', 'scripts', 'tests', ''):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
