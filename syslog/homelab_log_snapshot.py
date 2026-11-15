"""Test stand-in for the module install_snapshot.py installs on Proxmox (`/usr/local/bin/homelab_log_snapshot.py`).

syslog-monitor.py imports it by that plain name at request time; this shim re-exports the repo's
nightly/log_snapshot.py so the tracked monitor can be exercised with the repo's own code under `npm test`.
It is not what runs on Proxmox: install_snapshot.py writes the real module from nightly/log_snapshot.py.
"""
from nightly.log_snapshot import *  # noqa: F401,F403
from nightly import log_snapshot as _impl

snapshot = _impl.snapshot
group_recent = _impl.group_recent
