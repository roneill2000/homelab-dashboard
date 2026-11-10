#!/usr/bin/env python3
"""homelab-download-rpc: the only thing the dashboard's download key may run.

OpenSSH runs this as the forced command for the dashboard's download key
(/root/.ssh/authorized_keys on the n100). It reads SSH_ORIGINAL_COMMAND,
expects one JSON object, and answers with one JSON object: no shell is ever
started, no shell string is ever accepted, and every url, folder and file name
is validated by homelab_download_lib before anything touches the Unraid share.

Dashboard -> lib calls:   {"v":1,"cmd":"start","url":...,"dest":...,"name":...}
                          {"v":1,"cmd":"check","jobs":[{"dest":...,"name":...}, ...]}
                          {"v":1,"cmd":"stop","name":...}
                          {"v":1,"cmd":"ping"}
Answer:                   {"ok":true,...} or {"ok":false,"error":"..."}

Installed on .3 as /usr/local/sbin/homelab-download-rpc (repo copy:
scripts/homelab_download_rpc.py). See homelab_download_lib.py for the rules.
"""
import json
import os
import sys

# The library lives beside this file on the n100. HOMELAB_DOWNLOAD_LIB_DIR is
# only for the repo tests, which import the copy in scripts/.
LIB_DIR = os.environ.get('HOMELAB_DOWNLOAD_LIB_DIR') or '/usr/local/sbin'
sys.path.insert(0, LIB_DIR)
import homelab_download_lib as lib  # noqa: E402

MAX_REQUEST = 16000


def main() -> int:
    raw = (os.environ.get('SSH_ORIGINAL_COMMAND') or '').strip()
    if not raw or len(raw) > MAX_REQUEST:
        out = {'ok': False, 'error': 'this interface takes one JSON request'}
    else:
        try:
            req = json.loads(raw)
        except ValueError:
            out = {'ok': False, 'error': 'this interface takes one JSON request'}
        else:
            out = lib.rpc_reply(req)
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
