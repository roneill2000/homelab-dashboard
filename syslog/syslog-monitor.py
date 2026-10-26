#!/usr/bin/env python3
"""
Centralized Syslog Monitor, Automated Parser & Homelab Command API
Runs on Proxmox VE (192.168.0.3:8999)
"""

import os
import glob
import re
import json
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading

PORT = 8999
LOG_DIR = "/var/log/remote"

# Error detection patterns
CRITICAL_PATTERNS = [
    re.compile(r"(?i)\b(kernel:.*(panic|oops|bug:|hung_task|out of memory|oom-killer))\b"),
    re.compile(r"(?i)\b(ext4-fs error|blk_update_request: I/O error|mmc\d*: error|BTRFS error|XFS.*error)\b"),
    re.compile(r"(?i)\b(segfault at|core dumped|hardware error|mce: \[Hardware Error\])\b"),
    re.compile(r"(?i)\b(pihole-FTL.*(fatal|failed|terminated))\b"),
]

WARNING_PATTERNS = [
    re.compile(r"(?i)\b(Failed to start [a-zA-Z0-9_\-\.@]+service)\b"),
    re.compile(r"(?i)\b(Main process exited, code=exited, status=[1-9]\d*)\b"),
    re.compile(r"(?i)\b(link is down|carrier lost)\b"),
    re.compile(r"(?i)\b(DNS-HEALTH FAIL)\b"),
    re.compile(r"(?i)\b(deauthenticated due to local deauth request)\b"),
    re.compile(r"(?i)\b(read-only file system|journal commit I/O error)\b"),
]

lock = threading.Lock()
alerts = []          # list of dicts: {"timestamp": str, "host": str, "level": str, "msg": str, "ts": float}
file_positions = {}  # filepath: offset
cleared_before_ts = 0.0

ISO_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)")


def parse_line_ts(line, fallback_ts=None):
    """Parse ISO-8601 timestamp at start of syslog line into epoch seconds and display string."""
    m = ISO_TS_RE.match(line)
    if m:
        raw = m.group(1)
        try:
            dt = datetime.fromisoformat(raw)
            return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return (fallback_ts or time.time()), raw[:19].replace("T", " ")
    now = fallback_ts or time.time()
    return now, datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")


def scan_initial_logs():
    """Scan existing log files on startup for recent errors within the last 24 hours."""
    global alerts, file_positions
    cutoff = time.time() - 86400
    log_files = [f for f in glob.glob(os.path.join(LOG_DIR, "*.log")) if not os.path.islink(f)]

    for fpath in sorted(log_files):
        host = os.path.basename(fpath).replace(".log", "")
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 400000), os.SEEK_SET)
                if f.tell() > 0:
                    f.readline()  # discard partial line
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    check_line_for_alerts(host, line, cutoff_ts=cutoff)
                file_positions[fpath] = f.tell()
        except Exception:
            pass


def check_line_for_alerts(host, line, cutoff_ts=None):
    """Check a single log line against critical and warning patterns."""
    global alerts
    level = None
    for pattern in CRITICAL_PATTERNS:
        if pattern.search(line):
            level = "CRITICAL"
            break
    if not level:
        for pattern in WARNING_PATTERNS:
            if pattern.search(line):
                level = "WARNING"
                break

    if level:
        ts, ts_str = parse_line_ts(line)
        if cutoff_ts and ts < cutoff_ts:
            return
        if ts <= cleared_before_ts:
            return

        alert_entry = {
            "timestamp": ts_str,
            "host": host,
            "level": level,
            "msg": line,
            "ts": ts,
        }
        with lock:
            if not alerts or alerts[-1]["msg"] != line:
                alerts.append(alert_entry)
                if len(alerts) > 150:
                    alerts.pop(0)


def tail_logs_worker():
    """Worker thread that continuously polls for new log lines."""
    global file_positions
    while True:
        try:
            log_files = [f for f in glob.glob(os.path.join(LOG_DIR, "*.log")) if not os.path.islink(f)]
            for fpath in log_files:
                host = os.path.basename(fpath).replace(".log", "")
                if fpath not in file_positions:
                    file_positions[fpath] = os.path.getsize(fpath)
                    continue

                curr_size = os.path.getsize(fpath)
                last_pos = file_positions[fpath]

                if curr_size < last_pos:
                    last_pos = 0

                if curr_size > last_pos:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos)
                        for line in f:
                            line = line.strip()
                            if line:
                                check_line_for_alerts(host, line)
                        file_positions[fpath] = f.tell()
        except Exception:
            pass
        time.sleep(1.0)


def get_stats():
    """Calculate 24h stats for Homelab Command Center and Syslog UI."""
    now = time.time()
    cutoff_24h = max(now - 86400, cleared_before_ts)
    cutoff_active = now - 3600  # active within 60 mins (some quiet containers log hourly)

    log_files = [f for f in glob.glob(os.path.join(LOG_DIR, "*.log")) if not os.path.islink(f)]
    total_nodes = len(log_files)
    active_nodes = 0
    hosts_meta = []

    for fpath in sorted(log_files):
        h = os.path.basename(fpath).replace(".log", "")
        try:
            st = os.stat(fpath)
            is_active = st.st_mtime >= cutoff_active
            if is_active:
                active_nodes += 1
            hosts_meta.append({
                "host": h,
                "size_kb": round(st.st_size / 1024, 1),
                "last_seen_sec": max(0, int(now - st.st_mtime)),
                "active": is_active,
            })
        except Exception:
            pass

    with lock:
        recent_alerts = [a for a in alerts if a["ts"] >= cutoff_24h]
        crit_count = sum(1 for a in recent_alerts if a["level"] == "CRITICAL")
        warn_count = sum(1 for a in recent_alerts if a["level"] == "WARNING")
        last_alert_msg = "None"
        if recent_alerts:
            last = sorted(recent_alerts, key=lambda x: x["ts"])[-1]
            # Strip leading ISO timestamp from msg for cleaner display
            clean_msg = ISO_TS_RE.sub("", last["msg"]).strip()
            last_alert_msg = f"[{last['level']}] {last['host']}: {clean_msg}"
            if len(last_alert_msg) > 95:
                last_alert_msg = last_alert_msg[:92] + "..."

    status = "Healthy"
    if crit_count > 0:
        status = "Critical"
    elif warn_count > 0:
        status = "Warning"

    return {
        "status": status,
        "active_nodes": active_nodes,
        "total_nodes": total_nodes,
        "errors_24h": crit_count,
        "critical_24h": crit_count,
        "warnings_24h": warn_count,
        "total_alerts_24h": len(recent_alerts),
        "last_alert": last_alert_msg,
        "hosts": hosts_meta,
        "timestamp": datetime.now().isoformat(),
    }


class SyslogHandler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, format, *args):
        pass

    def _send_bytes(self, status, content_type, body_bytes, head_only=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head_only:
            self.wfile.write(body_bytes)

    def do_OPTIONS(self):
        self._send_bytes(204, "text/plain", b"", head_only=True)

    def do_HEAD(self):
        self.handle_get_or_head(head_only=True)

    def do_GET(self):
        self.handle_get_or_head(head_only=False)

    def do_POST(self):
        global alerts, cleared_before_ts
        parsed = urlparse(self.path)
        if parsed.path == "/api/clear":
            with lock:
                alerts.clear()
                cleared_before_ts = time.time()
            payload = json.dumps({"ok": True, "status": "Healthy", "cleared_at": cleared_before_ts}).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", payload)
            return
        self._send_bytes(404, "application/json", b'{"error":"not found"}')

    def handle_get_or_head(self, head_only=False):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # HOMELAB_NIGHTLY_SNAPSHOT_V1
        if path == '/api/nightly-snapshot':
            from pathlib import Path
            from homelab_log_snapshot import snapshot
            try:
                payload = snapshot(Path(LOG_DIR), CRITICAL_PATTERNS, WARNING_PATTERNS)
                self._send_bytes(200, 'application/json; charset=utf-8', json.dumps(payload).encode('utf-8'), head_only)
            except Exception:
                self._send_bytes(503, 'application/json', b'{"error":"Snapshot unavailable"}', head_only)
            return
        if path == "/api/status":
            stats = get_stats()
            self._send_bytes(200, "application/json; charset=utf-8", json.dumps(stats).encode("utf-8"), head_only)
            return

        if path == "/api/alerts":
            cutoff_24h = max(time.time() - 86400, cleared_before_ts)
            with lock:
                res = sorted([a for a in alerts if a["ts"] >= cutoff_24h], key=lambda x: x["ts"], reverse=True)[:60]
            self._send_bytes(200, "application/json; charset=utf-8", json.dumps(res).encode("utf-8"), head_only)
            return

        if path == "/api/logs":
            host = query.get("host", ["all"])[0]
            search = query.get("q", [""])[0].lower()
            limit = min(int(query.get("limit", [200])[0]), 500)

            lines = []
            log_files = [f for f in glob.glob(os.path.join(LOG_DIR, "*.log")) if not os.path.islink(f)]
            target_files = log_files if host == "all" else [os.path.join(LOG_DIR, f"{host}.log")]
            per_file_tail = 80000 if host == "all" else 250000

            for fpath in target_files:
                if not os.path.exists(fpath):
                    continue
                h_name = os.path.basename(fpath).replace(".log", "")
                try:
                    mtime = os.path.getmtime(fpath)
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - per_file_tail), os.SEEK_SET)
                        if f.tell() > 0:
                            f.readline()
                        file_lines = f.readlines()[-limit:]
                        for idx, raw_line in enumerate(file_lines):
                            line = raw_line.strip()
                            if not line:
                                continue
                            if search and search not in line.lower() and search not in h_name.lower():
                                continue
                            ts, _ = parse_line_ts(line, fallback_ts=mtime - (len(file_lines) - idx) * 0.001)
                            lines.append({"host": h_name, "line": line, "ts": ts})
                except Exception:
                    pass

            lines.sort(key=lambda x: x["ts"])
            lines = [{"host": x["host"], "line": x["line"]} for x in lines[-limit:]]
            self._send_bytes(200, "application/json; charset=utf-8", json.dumps(lines).encode("utf-8"), head_only)
            return

        if path in ("/", "/index.html"):
            body = self.render_dashboard().encode("utf-8")
            self._send_bytes(200, "text/html; charset=utf-8", body, head_only)
            return

        if path == "/favicon.ico":
            svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0f172a"/><circle cx="32" cy="32" r="14" fill="#38bdf8"/></svg>'
            self._send_bytes(200, "image/svg+xml", svg, head_only)
            return

        self._send_bytes(404, "text/plain", b"Not Found", head_only)

    def render_dashboard(self):
        log_files = [f for f in glob.glob(os.path.join(LOG_DIR, "*.log")) if not os.path.islink(f)]
        hosts = sorted([os.path.basename(f).replace(".log", "") for f in log_files])
        host_options = "".join([f'<option value="{h}">{h}</option>' for h in hosts])

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#060913">
<title>Syslog Telemetry & Incident Stream · 192.168.0.3:8999</title>
<style>
  :root {{
    --bg: #060913;
    --card: rgba(15, 23, 42, 0.78);
    --card-solid: #0f172a;
    --border: rgba(148, 163, 184, 0.14);
    --text: #f8fafc;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --healthy: #10b981;
    --warning: #f59e0b;
    --critical: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    background-image:
      radial-gradient(at 10% 0%, rgba(56, 189, 248, 0.12) 0px, transparent 45%),
      radial-gradient(at 90% 10%, rgba(99, 102, 241, 0.10) 0px, transparent 45%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    padding: 20px 24px 40px;
    min-height: 100vh;
  }}
  .container {{ max-width: 1520px; margin: 0 auto; }}
  .header {{
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;
    padding: 16px 20px; margin-bottom: 20px;
    background: var(--card); border: 1px solid var(--border); border-radius: 18px;
    backdrop-filter: blur(16px);
  }}
  .brand {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .title {{ font-size: 20px; font-weight: 800; letter-spacing: -0.02em; display: flex; align-items: center; gap: 10px; }}
  .badge {{ padding: 4px 11px; border-radius: 9999px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }}
  .badge-healthy {{ background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }}
  .badge-warning {{ background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }}
  .badge-critical {{ background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); }}

  .header-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .btn {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 14px; border-radius: 10px; font-size: 13px; font-weight: 600;
    border: 1px solid var(--border); background: rgba(30, 41, 59, 0.75); color: var(--text);
    cursor: pointer; text-decoration: none; transition: all 0.15s ease;
  }}
  .btn:hover {{ border-color: rgba(56, 189, 248, 0.45); background: rgba(56, 189, 248, 0.12); color: #7dd3fc; }}
  .btn-clear {{ border-color: rgba(16, 185, 129, 0.35); background: rgba(16, 185, 129, 0.12); color: #34d399; }}
  .btn-clear:hover {{ background: rgba(16, 185, 129, 0.22); border-color: #10b981; color: #fff; }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px; }}
  .stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px; }}
  .stat-label {{ color: var(--muted); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
  .stat-val {{ font-size: 26px; font-weight: 800; color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
  .stat-sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}

  .host-pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }}
  .host-pill {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
    background: var(--card); border: 1px solid var(--border); color: #cbd5e1; cursor: pointer;
  }}
  .host-pill.active {{ background: rgba(56, 189, 248, 0.16); border-color: #38bdf8; color: #fff; }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--healthy); }}
  .dot.idle {{ background: #64748b; }}

  .section-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 10px; flex-wrap: wrap; }}
  .section-title {{ font-size: 15px; font-weight: 700; color: #e2e8f0; display: flex; align-items: center; gap: 8px; }}

  .alerts-container {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 14px; margin-bottom: 20px; max-height: 220px; overflow-y: auto; }}
  .alert-row {{ padding: 8px 12px; border-radius: 8px; font-size: 12.5px; margin-bottom: 6px; display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .alert-critical {{ background: rgba(239, 68, 68, 0.14); border-left: 3px solid var(--critical); }}
  .alert-warning {{ background: rgba(245, 158, 11, 0.14); border-left: 3px solid var(--warning); }}

  .controls {{ display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }}
  select, input {{
    background: var(--card-solid); border: 1px solid var(--border); color: var(--text);
    padding: 10px 14px; border-radius: 10px; font-size: 13.5px; outline: none;
  }}
  input {{ flex: 1; min-width: 220px; }}
  select:focus, input:focus {{ border-color: var(--accent); }}

  .logs-terminal {{
    background: #030712; border: 1px solid var(--border); border-radius: 16px;
    padding: 14px 16px; font-size: 12.5px; line-height: 1.65; height: 540px;
    overflow-y: auto; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }}
  .log-line {{ padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }}
  .log-host {{ color: var(--accent); font-weight: 700; margin-right: 8px; }}
  .log-warn {{ color: #fbbf24; background: rgba(245, 158, 11, 0.06); }}
  .log-crit {{ color: #f87171; font-weight: 700; background: rgba(239, 68, 68, 0.1); }}

  @media (max-width: 768px) {{
    body {{ padding: 12px 12px 28px; }}
    .header {{ padding: 14px; }}
    .logs-terminal {{ height: 420px; font-size: 11.5px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="brand">
      <div class="title">
        <span>📡 Central Syslog Collector</span>
        <span id="status-badge" class="badge badge-healthy">Healthy</span>
      </div>
      <span style="font-size: 12px; color: var(--muted);" id="last-update">Connecting...</span>
    </div>
    <div class="header-actions">
      <button class="btn btn-clear" id="clear-btn" onclick="clearAlerts()">✓ Acknowledge & Clear Alerts</button>
      <button class="btn" id="pause-btn" onclick="togglePause()">⏸️ Pause Stream</button>
      <a class="btn" href="http://192.168.0.2">← Homelab Command (.2)</a>
    </div>
  </div>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-label">Reporting Nodes (60m)</div>
      <div class="stat-val" id="stat-nodes">-- / --</div>
      <div class="stat-sub">UDP :514 Rsyslog Collector</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Critical Errors (24h)</div>
      <div class="stat-val" id="stat-crit" style="color: var(--healthy);">0</div>
      <div class="stat-sub">Kernel / FS / Service Crashes</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Warnings (24h)</div>
      <div class="stat-val" id="stat-warn">0</div>
      <div class="stat-sub">Link Transitions / Wi-Fi Deauth</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Latest Incident</div>
      <div class="stat-val" style="font-size: 13px; font-weight: 500; margin-top: 4px; line-height: 1.4; word-break: break-word;" id="stat-last-alert">None</div>
    </div>
  </div>

  <div class="host-pills" id="host-pills"></div>

  <div class="section-head">
    <div class="section-title">🚨 Anomaly Alerts (Last 24h)</div>
  </div>
  <div class="alerts-container" id="alerts-box">
    <div style="color: var(--muted); font-size: 13px;">No anomalies detected. All systems reporting nominal.</div>
  </div>

  <div class="section-head">
    <div class="section-title">📜 Unified Real-Time Syslog Stream</div>
  </div>
  <div class="controls">
    <select id="host-filter">
      <option value="all">All Devices (Chronological Merged Stream)</option>
      {host_options}
    </select>
    <input type="text" id="search-input" placeholder="Filter logs by keyword (e.g. error, wlan, pihole, wireguard, smartd)...">
  </div>

  <div class="logs-terminal" id="terminal">Loading real-time syslog stream...</div>
</div>

<script>
  let autoScroll = true;
  let streamPaused = false;
  const terminal = document.getElementById('terminal');
  const hostFilter = document.getElementById('host-filter');
  const searchInput = document.getElementById('search-input');

  terminal.addEventListener('scroll', () => {{
    autoScroll = (terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight) < 50;
  }});

  function selectHostPill(h) {{
    hostFilter.value = h;
    updateLogs();
    renderHostPills(window.__LAST_HOSTS__ || []);
  }}

  function renderHostPills(hosts) {{
    window.__LAST_HOSTS__ = hosts;
    const cur = hostFilter.value;
    const container = document.getElementById('host-pills');
    const allPill = `<div class="host-pill ${{cur === 'all' ? 'active' : ''}}" onclick="selectHostPill('all')"><span class="dot"></span>All Hosts (${{hosts.length}})</div>`;
    const pills = hosts.map(h => {{
      const age = h.last_seen_sec < 60 ? `${{h.last_seen_sec}}s` : `${{Math.round(h.last_seen_sec/60)}}m`;
      return `<div class="host-pill ${{cur === h.host ? 'active' : ''}}" onclick="selectHostPill('${{h.host}}')">
        <span class="dot ${{h.active ? '' : 'idle'}}"></span>
        <span>${{h.host}}</span>
        <span style="color:var(--muted); font-size:11px;">${{age}}</span>
      </div>`;
    }}).join('');
    container.innerHTML = allPill + pills;
  }}

  async function clearAlerts() {{
    try {{
      await fetch('/api/clear', {{ method: 'POST' }});
      await updateStats();
      await updateAlerts();
    }} catch (e) {{}}
  }}

  function togglePause() {{
    streamPaused = !streamPaused;
    const btn = document.getElementById('pause-btn');
    btn.innerText = streamPaused ? '▶️ Resume Stream' : '⏸️ Pause Stream';
    if (!streamPaused) updateLogs();
  }}

  function escHtml(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}

  async function updateStats() {{
    try {{
      const res = await fetch('/api/status');
      const data = await res.json();
      document.getElementById('stat-nodes').innerText = `${{data.active_nodes}} / ${{data.total_nodes}}`;
      const critEl = document.getElementById('stat-crit');
      critEl.innerText = data.critical_24h;
      critEl.style.color = data.critical_24h > 0 ? 'var(--critical)' : 'var(--healthy)';

      const warnEl = document.getElementById('stat-warn');
      warnEl.innerText = data.warnings_24h;
      warnEl.style.color = data.warnings_24h > 0 ? 'var(--warning)' : 'var(--text)';

      document.getElementById('stat-last-alert').innerText = data.last_alert;

      const badge = document.getElementById('status-badge');
      badge.className = 'badge badge-' + data.status.toLowerCase();
      badge.innerText = data.status;
      document.getElementById('last-update').innerText = 'Updated ' + new Date().toLocaleTimeString();
      if (data.hosts) renderHostPills(data.hosts);
    }} catch (e) {{}}
  }}

  async function updateAlerts() {{
    try {{
      const res = await fetch('/api/alerts');
      const list = await res.json();
      const box = document.getElementById('alerts-box');
      if (list.length === 0) {{
        box.innerHTML = '<div style="color: #34d399; font-size: 13px; font-weight: 600;">✓ Zero active anomalies. All monitored homelab nodes reporting nominal.</div>';
        return;
      }}
      box.innerHTML = list.map(a => `
        <div class="alert-row alert-${{a.level.toLowerCase()}}">
          <span style="font-weight:700;">[${{escHtml(a.level)}}]</span>
          <span style="color:var(--muted);">${{escHtml(a.timestamp)}}</span>
          <span style="color:var(--accent); font-weight:700;">${{escHtml(a.host)}}:</span>
          <span style="flex:1;">${{escHtml(a.msg)}}</span>
        </div>
      `).join('');
    }} catch (e) {{}}
  }}

  async function updateLogs() {{
    if (streamPaused) return;
    const host = hostFilter.value;
    const q = searchInput.value;
    try {{
      const res = await fetch(`/api/logs?host=${{encodeURIComponent(host)}}&q=${{encodeURIComponent(q)}}&limit=200`);
      const logs = await res.json();
      if (logs.length === 0) {{
        terminal.innerHTML = '<div style="color:var(--muted);">No matching log lines found.</div>';
        return;
      }}
      terminal.innerHTML = logs.map(item => {{
        let cls = 'log-line';
        if (/panic|oops|fatal|critical|segfault|oom-killer/i.test(item.line)) cls += ' log-crit';
        else if (/error|warn|fail|dropped|link is down|deauthenticated/i.test(item.line)) cls += ' log-warn';
        return `<div class="${{cls}}"><span class="log-host">[${{escHtml(item.host)}}]</span>${{escHtml(item.line)}}</div>`;
      }}).join('');
      if (autoScroll) {{
        terminal.scrollTop = terminal.scrollHeight;
      }}
    }} catch (e) {{}}
  }}

  hostFilter.addEventListener('change', () => {{
    renderHostPills(window.__LAST_HOSTS__ || []);
    updateLogs();
  }});
  searchInput.addEventListener('input', updateLogs);

  setInterval(updateStats, 4000);
  setInterval(updateAlerts, 5000);
  setInterval(updateLogs, 3000);

  updateStats();
  updateAlerts();
  updateLogs();
</script>
</body>
</html>
"""


class ThreadedSyslogServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    scan_initial_logs()
    t = threading.Thread(target=tail_logs_worker, daemon=True)
    t.start()
    server = ThreadedSyslogServer(("0.0.0.0", PORT), SyslogHandler)
    print(f"Syslog Monitor (Threaded) listening on port {PORT}...")
    server.serve_forever()


if __name__ == "__main__":
    main()
