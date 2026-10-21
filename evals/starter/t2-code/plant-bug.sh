#!/bin/sh
# Run inside a throwaway copy of ~/homelab. Plants one bug that test_updates.py catches.
set -e
sed -i.bak "s/        if line == 'U' or line.startswith('U '):  # bare 'U' when output stripping removed the space/        if line.startswith('U '):/" updates/homelab_update.py
rm -f updates/homelab_update.py.bak
python3 -m unittest test_updates >/dev/null 2>&1 && { echo "bug NOT planted (tests still pass)"; exit 1; } || echo "bug planted: test_updates fails"
