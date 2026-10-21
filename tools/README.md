# Read-only homelab tools

Installed 19 October 2026, authorized by Ryan's “Read-only diagnostics first” choice.

In [Open WebUI](http://192.168.0.16:3000), start a new chat with any of the three desktop model presets. **Homelab diagnostics** is enabled by default; refresh an already-open browser first. Example: “Check Unraid cache free space and recent warnings. Use the diagnostic tools and explain what you actually observed.” Existing chats may need the tool selected from the Tools menu.

The model can choose `proxmox` or `unraid` and one of five checks:

| Check | Proxmox | Unraid |
| --- | --- | --- |
| summary | Uptime, RAM, root/scratch free space | Uptime, RAM, cache/array free space |
| storage | Block devices and filesystem capacity | Block devices, capacity, md status |
| services | Failed systemd units | Docker status and published ports |
| guests | LXC and VM status | Docker container status |
| warnings | Up to 60 warning-or-higher journal entries from 24h | Last 60 matching lines within last 2,000 syslog lines |

Missing or timed-out data is reported as unknown. Tool results are timestamped, truncated to 16KB and scrubbed for common password/token patterns. Redaction is best effort; logs are treated as untrusted data. No arbitrary commands, file reads, restarts or configuration changes are exposed.

## Enforcement and locations

Open WebUI's owner-only native Python Tool calls a token-authenticated broker on **172.18.0.1:8766**, its Docker bridge. No broker listener exists on the LAN address. LXC112 runs `homelab-tools.service` as non-root user `homelab-tools`. State and the dedicated SSH key are in `/var/lib/homelab-tools`, mode 0700. The broker token is also in Open WebUI's server-side tool valves; it is not part of the model's tool schema.

Both SSH endpoints enforce the same command allowlist independently using a `restrict,from="192.168.0.16",command="..."` authorized-key entry. The root login can only execute the fixed wrapper; it cannot open an unrestricted shell, forward ports or select arbitrary commands. Private keys remain in LXC112. Host keys were obtained through the existing trusted administrative SSH connections, then pinned with strict host-key checking.

- Proxmox wrapper: `/usr/local/libexec/homelab-diagnostics.sh`.
- Unraid wrapper: `/boot/config/homelab-tools/diagnostics.sh`; authorized key is in persistent `/boot/config/ssh/root/authorized_keys` through `/root/.ssh` symlink.
- Broker: `/usr/local/bin/homelab-tools.py`, systemd unit `/etc/systemd/system/homelab-tools.service`.
- Open WebUI tool ID: `homelab_diagnostics`; default `meta.toolIds` on `qwen3.8-27b`, `qwen3.6-35b-moe`, and `gemma-4-26b-a4b`. Existing sampling/medium defaults are preserved. Model history records these edits.
- Open WebUI private backups inside 112: `/opt/open-webui/backups/20261019T230355Z/webui.db` before installation, then `/opt/open-webui/backups/20261019T230644Z/webui.db` before the final settings verification.

The frontend tool default is not an API default: API clients must supply `tool_ids`. These SSH tools belong to Open WebUI. The separate A310 Homepage prompt retrieves expanded read-only dashboard observations and retained warning logs; it does not use this SSH broker. The desktop still needs to be on for its models to answer; broker availability alone does not supply inference.

## Maintenance and verification

`deploy.py` provisions the service/key and appends only its dedicated forced-command entries; `install_webui.py` uses Open WebUI's versioned model/tool persistence methods. No existing service was stopped for installation. The broker expects the current Open WebUI Docker bridge gateway 172.18.0.1; check that mapping after changing its Compose network.

Three unit tests verify injection rejection before SSH, fixed command arguments, timeout and bounded redaction. Live checks passed for all five commands on both hosts; direct SSH `id` and broker shell-injection requests were rejected. Native Tool invocation inside the Open WebUI container returned both host summaries. `verify_chat.py` exercises a single temporary native-tool chat and deletes only that test chat afterward.

To disable access: stop/disable `homelab-tools.service` in 112, remove `homelab_diagnostics` from model tool defaults, and remove only the authorized-key lines labeled `homelab-readonly-diagnostics` on both hosts. Keep unrelated SSH keys. No new approval-based mutation tools are installed.

Sources: [Open WebUI tools](https://docs.openwebui.com/features/extensibility/plugin/tools/), [native API tool loop](https://docs.openwebui.com/reference/server-side-tool-calling/).

## Gemma replacement — 20 October 2026

Ryan replaced Qwen Coder with `gemma-4-26b-a4b`. The old Open WebUI profile is inactive, with its history retained. Gemma inherits its router sampling settings; only the default diagnostics tool was added. The other models retain their settings, including 27B medium reasoning. Pre-change database backup: `/opt/open-webui/backups/20261020T133703Z/webui.db`.

A complete Gemma chat passed: its stored output contained `function_call`, `function_call_output`, and the final answer, including a successful timestamped Unraid observation. The temporary test chat was deleted. Evidence remains in the private file `/opt/open-webui/homelab-verification/result.log` inside LXC112. `run_verification.py` runs the test under a bounded transient systemd service so laptop disconnections cannot lose the result. Qwen MoE and 27B have the tool enabled but were not verified end to end in this session. Earlier Coder native-call attempts printed XML instead of invoking a tool; that model has since been retired by Ryan.


## Warning-read reliability — 20 October 2026

The forced-command script now uses Bash pipefail and an awk filter that succeeds for a genuine empty match. Missing/unreadable syslog therefore returns failure to the broker instead of an empty successful response. Six unit tests cover the broker and warning pipeline, including missing input and the last-60-match bound. Live warning checks on both hosts passed. Original wrappers are preserved as `.bak-20261020T100045` beside their deployed paths.

LXC112 is now included in the existing daily03:00 Proxmox backup job, keep-last3 on Unraid. Its first archive and full network-isolated restore passed20October; the restored broker started and Open WebUI/database checks passed. No live remote SSH command was attempted from the isolated restore.

## Smaller results and an overview check — 20 October 2026

Ryan's question "How is my homelab doing right now?" to qwen3.6-35b-moe made 6 tool calls and hit `request (17624 tokens) exceeds the available context size (16384 tokens)`. Changes:

- New `overview` check (host `all`). The broker summarizes Homepage `/api/telemetry` and `/api/fleet` in one call (live: 901 characters): host load, services down, DNS, log counts, unhealthy drives, failed units, bad mounts, stopped Docker containers, pending/failed updates. No SSH. The tool description tells models to start there and drill down only when something is flagged.
- `warnings` collapses repeated lines to one line with a count (most frequent first, 25 max, 220 characters each). Live: Proxmox 7,554 → 3,227 characters, Unraid 9,351 → 3,836.
- Every result is capped at 6,000 characters (was 16,000).
- `install_webui.py --update` replaces the tool code and specs in Open WebUI's database. Without the flag, a changed tool is still refused. Open WebUI reloads tool code when the stored content changes, so no restart is needed. Pre-update DB backup: `/opt/open-webui/backups/20261020T184638Z`.

Tests: `test_broker` (overview compact and flags problems, 6,000 bound) and `test_diagnostics` (repeats collapsed, 25-line bound) pass. The original question has not been re-asked through Open WebUI.
