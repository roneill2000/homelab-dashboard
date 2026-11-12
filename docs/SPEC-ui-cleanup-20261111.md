# Spec: four small UI and wording fixes (11 Nov 2026)

From a read-only review of the live dashboard. No behaviour changes: wording, labels and two table cells. One commit.

## 1. Infrastructure: stale note about stopped VMs

`src/app.jsx:171`, Recovery readiness footnote.

- Now: "Not backed up off-site, by choice: the media library, the Private share and the stopped VMs 100, 101, 109 and 110."
- Those VMs were destroyed on 8 Nov.
- Change to: "Not backed up off-site, by choice: the media library and the Private share."

## 2. Network map: wrong names, missing hosts

The map is `ip_matrix`, built from the `known` table in the live backend (`/usr/local/bin/homelab-dashboard.py`, around line 459). That file is not in the repo, so change it with a patch function in `backend/backend_patch.py`, like `patch_opencode` does for the `pi` row.

Rename:

| Octet | Now | Change to |
|---|---|---|
| .1 | GL-BE9300 Router | Flint 3 |
| .3 | Proxmox VE 8 | Proxmox VE 9 |

Add (role, link):

| Octet | Name | Role | Link |
|---|---|---|---|
| .12 | Gaming PC | RTX 5070 Ti | none, or `https://strata.lan` |
| .20 | Caddy proxy | LXC 115 | `http://proxy.lan` |
| .22 | Roam 6 | Living-room Wi-Fi | `http://192.168.0.22` |

Notes:
- New rows have no TCP check of their own. Pass `False` as the check so "online" comes from `ip in active_ips` (the existing fallback). The PC will then show offline while asleep, which is correct.
- The loop iterates a fixed octet list (`[1, 2, 3, ... 254, 255]`); add 12, 20, 22 there, and keep the list in address order so `.21` (key 255) sorts between `.20` and `.22`. Today `pi` renders last, after `.254`.
- The patch must be safe to run twice (deploy re-patches).

## 3. "VPN host" service tile

Service key `wireguard_ssh`. The tile reads "VPN host · Host presence · .30; logs" and opens `http://192.168.0.3:8999` (the syslog viewer). WireGuard is now LXC 117 `wgvpn`.

- Name: "WireGuard"
- Description: "VPN gateway · LXC 117"
- Detail text on the right: `192.168.0.30`
- Link: none. LXC 117 has no web page. If the tile component requires a link, use `https://proxmox.lan`.
- Same link change for the `.30` row in the network map (item 2), which also points at `:8999`.

The probe itself (`wg_vm_up or .30 in active_ips`) stays as is.

## 4. Local AI request history

`src/app.jsx:304-309`, both tables.

**a. "Error 200".** A request that returned HTTP 200 but produced no tokens is stored with `ok: 0, status: 200`, and the First output cell prints "Error 200".
- When `ok` is false and `status` is 200: show "No answer".
- Other failures keep "Error <status>". Rows with `swap = 'loop'` (status 409) show "Stopped: repeated step".

**b. "First output 0 s" on every Strata row.** `ttfb_s` is the time to the first byte. Strata and the Coder send their first bytes at once (0.02 s), before reading the prompt, so the column says 0 s for a request that waited 7 s.
- In both tables, for engines `strata` and `coder`: show prompt reading time instead, `prompt_n / prompt_tps`, when both are present; otherwise "—".
- llama-server rows keep `ttfb_s` (it is real there).
- Update the "About these numbers" text to say so in one sentence.
- Not in scope: making the broker time the first content token. That would be the proper fix and can follow later.

## Checks

- `npm run build`, `npm run check` (`ALL CHECKS OK`), `npm test`.
- Add or extend a test for the new patch function in item 2 (applies once, second run is a no-op, row order).
- After deploy: Network map shows 20 rows in address order; no "VE 8", "GL-BE9300" or "109 and 110" anywhere in the page (`content-check` is a good place for these three strings).
