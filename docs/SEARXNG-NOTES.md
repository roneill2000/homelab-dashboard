# SearXNG · search.lan (8 Nov 2026)

## Where it lives
- Docker container `searxng` in LXC 102 (utilities, .13), image `searxng/searxng:2026.10.24-ef0ec03da`, listening `0.0.0.0:8888`.
- Settings bind-mounted at 102 `/opt/searxng/config/settings.yml` — survives container recreate; changes need `docker restart searxng`.
- LAN only: `limiter: false`, `public_instance: false`, secret key in settings.yml.

## Engine tuning (measured 7 Nov)
- Enabled: brave, google, bing, yahoo + wikipedia (wikipedia answers as an info box, not results — normal).
- Disabled: wikidata (timed out on most queries), qwant (refuses this IP), duckduckgo left default but auto-pauses on CAPTCHA (shows in `unresponsive_engines`).
- Startpage/Mojeek do not exist under those names in this SearXNG build; Yahoo/Qwant were the nearest substitutes, Qwant then dropped.
- `outgoing.request_timeout: 4.0`, `max_request_timeout: 8.0` — slow engines get dropped instead of stalling the answer.
- Measured: ~0.7-0.8 s per search, 39-47 results. Verified through the proxy on 8 Nov: 41 results via `search.lan`.
- JSON output enabled (`formats: [html, json]`) for Open WebUI/Hermes/Strata use later.

## search.lan
- Caddy (LXC 115): `search.lan { reverse_proxy 192.168.0.13:8888 }`, placed after hermes.lan. Repo `proxy/Caddyfile` now byte-identical to live.
- DNS: added `"192.168.0.20 search.lan"` to `[[dns.hosts]]` in `/etc/pihole/pihole.toml` on **both** .11 (primary) and .10 (replica Orange Pi). `/etc/pihole/hosts/custom.list` is auto-generated from pihole.toml — never edit it directly; restart `pihole-FTL` after editing. Backups `.bak-20261108-search` on both.
- Caddy picked up the local-CA cert for search.lan automatically (`local_certs`).

## Dashboard tile (LXC 104)
- Probe added to `port_targets` in live `/usr/local/bin/homelab-dashboard.py`: `'searxng': ('192.168.0.13', 8888)` (same TCP pattern as hermes). Restarted `homelab-dashboard.service`; `/api/telemetry` shows `services.searxng: true`, counts 25/25.
- Tile row embedded in `/opt/homelab-dashboard/index.html` and id added to `/opt/homelab-dashboard/catalog.json`.
- NOTE: the live collector base file is not in the repo (deploy.py patches it in place) — the port_targets edit exists only live until the base is next resynced.

## Pitfall hit during this work
`systemctl reload caddy` on a long-running caddy can panic (`context: internal error: missing cancel error` — Go reload race) and kill the proxy: full reload of a 1w14h instance on 7 Nov took *.lan down for ~2 min. For config changes on this box use `systemctl restart caddy`, not reload. `caddy validate` passing does not prevent it.

## Live-before-repo drift corrected (8 Nov)
Repo was behind: missing hermes.lan, strata.lan, proxy.lan blocks (and still had removed kasm.lan/opencode.lan); catalog missing hermes, still had openhands. Both files re-synced to live + searxng; `node build.mjs` regenerates catalog.json.

## Not done
Hooking SearXNG into Open WebUI / Hermes / Strata search paths — engine tuning deliberately made JSON available for that, but no client config changed yet.
