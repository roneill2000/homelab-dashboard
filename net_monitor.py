"""Network throughput per machine (Task Manager style): 5 s samples, 24 h history."""
import json
import threading
import time
import urllib.request

HOSTS = (  # id, name, source url, kind
    ('proxmox', 'Proxmox', 'http://192.168.0.3:61209/', 'counters'),
    ('unraid', 'Unraid', 'http://192.168.0.4:61208/api/4/network', 'glances'),
    ('orangepi', 'Orange Pi', 'http://192.168.0.10:61209/', 'counters'),
)
_lock = threading.Lock()
_latest = {}
_prev = {}


def initialize(connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS net_samples (id TEXT, ts INTEGER, rx_bps REAL, tx_bps REAL, PRIMARY KEY(id, ts))')


def read(url, kind):
    with urllib.request.urlopen(url, timeout=3) as r:
        data = json.loads(r.read(65536))
    if kind == 'glances':  # list of interfaces; Unraid's uplink is eth0
        row = next((x for x in data if isinstance(x, dict) and x.get('interface_name') == 'eth0'), {})
        speed = row.get('speed')
        # Glances v4: bytes_sent/recv are per-refresh deltas; the *_gauge fields are the cumulative counters.
        return dict(rx=row.get('bytes_recv_gauge'), tx=row.get('bytes_sent_gauge'), speed_mbps=int(speed / 1048576) if speed else None)
    return dict(rx=data.get('rx_bytes'), tx=data.get('tx_bytes'), speed_mbps=data.get('speed_mbps'))


def rates(prev, cur, seconds):
    """Bits per second; None across a counter reset or a gap, never a fake zero."""
    if not prev or seconds <= 0 or seconds > 60:
        return None, None
    out = []
    for k in ('rx', 'tx'):
        a, b = prev.get(k), cur.get(k)
        out.append((b - a) * 8 / seconds if isinstance(a, int) and isinstance(b, int) and b >= a else None)
    return tuple(out)


def sample(connect, now):
    rows = []
    for key, name, url, kind in HOSTS:
        try:
            cur = read(url, kind)
            ok = isinstance(cur.get('rx'), int) and isinstance(cur.get('tx'), int)
        except Exception:
            cur, ok = {}, False
        prev = _prev.get(key)
        rx, tx = rates(prev[1] if prev else None, cur, now - prev[0] if prev else 0) if ok else (None, None)
        if ok:
            _prev[key] = (now, cur)
        with _lock:
            _latest[key] = dict(id=key, name=name, online=ok, rx_bps=rx, tx_bps=tx, link_mbps=cur.get('speed_mbps'), sampled_at=now if ok else _latest.get(key, {}).get('sampled_at'))
        if rx is not None:
            rows.append((key, int(now), rx, tx))
    with connect() as db:
        db.executemany('INSERT OR REPLACE INTO net_samples VALUES (?,?,?,?)', rows)
        db.execute('DELETE FROM net_samples WHERE ts<?', (int(now) - 86400,))


def start(connect):
    initialize(connect)
    def loop():
        while True:
            try:
                sample(connect, time.time())
            except Exception as exc:
                print('Network collector:', type(exc).__name__, flush=True)
            time.sleep(5)
    threading.Thread(target=loop, name='net-history', daemon=True).start()


def snapshot(connect, window, bucket, aggregate):
    totals = {}
    with connect() as db:
        rows = db.execute('SELECT id, ts, rx_bps, tx_bps FROM net_samples WHERE ts>=? ORDER BY id, ts', (int(time.time()) - window,)).fetchall()
        # Data moved = sum of 5 s rate samples; gaps add nothing, so totals are a floor. coverage = share of the period sampled.
        for label, seconds in (('1h', 3600), ('24h', 86400)):
            for key, rx, tx, n in db.execute('SELECT id, SUM(rx_bps), SUM(tx_bps), COUNT(*) FROM net_samples WHERE ts>=? GROUP BY id', (int(time.time()) - seconds,)):
                totals.setdefault(key, {})[label] = dict(rx_bytes=(rx or 0) * 5 / 8, tx_bytes=(tx or 0) * 5 / 8, coverage=round(min(1, n * 5 / seconds), 2))
    hist = aggregate(rows, bucket, metrics=('rx_bps', 'tx_bps'))
    with _lock:
        latest = {k: dict(v) for k, v in _latest.items()}
    out = []
    for key, name, *_ in HOSTS:
        item = latest.get(key, dict(id=key, name=name, online=False))
        item['history'] = hist.get(key, [])
        item['totals'] = totals.get(key, {})
        out.append(item)
    return dict(hosts=out, sample_seconds=5, bucket_seconds=bucket, source='Interface byte counters (Proxmox enp2s0, Unraid eth0, Pi end0)')
