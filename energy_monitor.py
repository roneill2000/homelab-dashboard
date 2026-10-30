"""Energy and cost: watts from host energy counters (RAPL, Arc, PC GPU) plus labelled estimates for what has no meter.
Hourly Wh per device is kept for 400 days in the dashboard database. Nothing here measures power at the wall: every
device total is an estimate built from the measured parts, the assumptions in /etc/homelab-dashboard/energy.json and a
power-supply efficiency, and the API says which part is which."""
import calendar
import copy
import json
from pathlib import Path
import threading
import time
import urllib.request

CONFIG = Path('/etc/homelab-dashboard/energy.json')
PC_GPU_URL = 'http://192.168.0.12:9835/gpu'
RETENTION = 400 * 86400
MAX_GAP = 30  # seconds; longer gaps between collector ticks are not filled in
FRESH = 60    # a host probe reading older than this is not used
# Typical figures for this hardware; energy.json overrides any of them. rate_per_kwh stays private (null = no cost shown).
DEFAULTS = dict(
    rate_per_kwh=None, rate_label=None,
    proxmox=dict(base_w=6.0, psu_efficiency=0.88),
    unraid=dict(base_w=10.0, psu_efficiency=0.88, hdd_active_w=6.5, hdd_standby_w=0.9, ssd_w=1.0),
    pc=dict(rest_awake_w=55.0, gpu_unmeasured_w=20.0, asleep_w=2.0, psu_efficiency=0.90),
    fixed=[dict(id='router', name='Router', detail='GL.iNet Flint 3', w=12.0),
           dict(id='orangepi', name='Orange Pi', detail='Secondary DNS', w=2.5)])

_lock = threading.Lock()
_prev = {}      # host -> (sampled_at, {counter: (uj, max_uj)})
_watts = {}     # host -> (sampled_at, {counter: watts})
_latest = {}
_last_tick = None


def config():
    out = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return out
    for k, v in raw.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict): out[k].update(v)
        elif k in out: out[k] = v
    return out


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def probe(payload):
    """The homelab AMP result from a Glances /amps answer, or None."""
    try:
        amp = next(a for a in (payload or {}).get('amps', []) if a.get('name', '').lower() == 'homelab')
        return json.loads(amp['result'])
    except Exception:
        return None


def counters(energy):
    out = {name: (z.get('uj'), z.get('max_uj')) for name, z in ((energy or {}).get('rapl') or {}).items() if isinstance(z, dict)}
    if number((energy or {}).get('gpu_uj')): out['gpu'] = (energy['gpu_uj'], None)
    return {k: v for k, v in out.items() if number(v[0])}


def rates(prev, cur, seconds):
    """Watts per counter from two cumulative microjoule readings; a wrapped counter uses its range, a reset is unknown."""
    if seconds <= 0 or seconds > 120: return {}
    out = {}
    for k, (uj, top) in cur.items():
        if k not in prev: continue
        delta = uj - prev[k][0]
        if delta < 0: delta = delta + top if number(top) and top > 0 else None
        if delta is not None and delta / 1e6 / seconds < 1000: out[k] = delta / 1e6 / seconds
    return out


def host_watts(host, reading, now):
    """Update counter rates for one host from its probe reading; returns the latest fresh rates or None."""
    stamp = (reading or {}).get('sampled_at')
    cur = counters((reading or {}).get('energy'))
    if number(stamp) and cur:
        prev = _prev.get(host)
        if prev and stamp > prev[0]:
            w = rates(prev[1], cur, stamp - prev[0])
            if w: _watts[host] = (stamp, w)
        if not prev or stamp > prev[0]: _prev[host] = (stamp, cur)
    last = _watts.get(host)
    return last[1] if last and now - last[0] <= FRESH else None


def device(id, name, detail, measured, estimated, efficiency, state='on', parts=None):
    """measured/estimated are DC-side watts; the wall figure adds power-supply loss."""
    if measured is None: return dict(id=id, name=name, detail=detail, state='unknown', watts=None, measured_w=None, parts=parts or [])
    eff = efficiency if number(efficiency) and 0.5 <= efficiency <= 1 else 1
    return dict(id=id, name=name, detail=detail, state=state, watts=(measured + estimated) / eff, measured_w=measured, parts=parts or [])


def readings(hosts, pc_gpu, pc_online, now, cfg):
    pve = host_watts('pve', probe(hosts.get('192.168.0.3')), now)
    unraid_probe = probe(hosts.get('192.168.0.4'))
    nas = host_watts('unraid', unraid_probe, now)
    out = []
    c = cfg['proxmox']
    if pve and 'package-0' in pve:
        out.append(device('proxmox', 'Proxmox', 'Intel N100', pve['package-0'], c['base_w'], c['psu_efficiency'],
                          parts=[dict(label='CPU package', w=pve['package-0'], measured=True), dict(label='Board, RAM, SSDs', w=c['base_w'], measured=False)]))
    else: out.append(device('proxmox', 'Proxmox', 'Intel N100', None, 0, 1))
    c = cfg['unraid']
    if nas and 'package-0' in nas:
        disks = ((unraid_probe or {}).get('energy') or {}).get('disks') or []
        hdd = [d for d in disks if d.get('rotational')]
        spinning = sum(1 for d in hdd if not d.get('spundown'))
        disk_w = spinning * c['hdd_active_w'] + (len(hdd) - spinning) * c['hdd_standby_w'] + (len(disks) - len(hdd)) * c['ssd_w']
        measured = nas['package-0'] + nas.get('dram', 0) + nas.get('gpu', 0)
        parts = [dict(label='CPU package', w=nas['package-0'], measured=True)]
        if 'dram' in nas: parts.append(dict(label='RAM', w=nas['dram'], measured=True))
        if 'gpu' in nas: parts.append(dict(label='Arc A310', w=nas['gpu'], measured=True))
        parts += [dict(label=f'Disks ({spinning} of {len(hdd)} HDDs spinning)', w=disk_w, measured=False), dict(label='Board and fans', w=c['base_w'], measured=False)]
        out.append(device('unraid', 'Unraid', 'i7-7700T · Arc A310 · disks', measured, disk_w + c['base_w'], c['psu_efficiency'], parts=parts))
    else: out.append(device('unraid', 'Unraid', 'i7-7700T · Arc A310 · disks', None, 0, 1))
    c = cfg['pc']
    gpu_w = (pc_gpu or {}).get('power_w')
    if number(gpu_w) and 0 <= gpu_w < 1000:
        out.append(device('pc', 'Gaming PC', 'Awake · RTX 5070 Ti', gpu_w, c['rest_awake_w'], c['psu_efficiency'], 'awake',
                          parts=[dict(label='GPU', w=gpu_w, measured=True), dict(label='CPU, board, SSDs', w=c['rest_awake_w'], measured=False)]))
    elif pc_online:
        out.append(device('pc', 'Gaming PC', 'Awake · GPU reading unavailable', 0, c['gpu_unmeasured_w'] + c['rest_awake_w'], c['psu_efficiency'], 'awake',
                          parts=[dict(label='Whole PC (no GPU reading)', w=c['gpu_unmeasured_w'] + c['rest_awake_w'], measured=False)]))
    else:
        out.append(dict(id='pc', name='Gaming PC', detail='Asleep or off', state='asleep', watts=c['asleep_w'], measured_w=0,
                        parts=[dict(label='Standby', w=c['asleep_w'], measured=False)]))
    for f in cfg['fixed']:
        if isinstance(f, dict) and number(f.get('w')):
            out.append(dict(id=str(f.get('id')), name=str(f.get('name')), detail=str(f.get('detail') or ''), state='on', watts=f['w'], measured_w=0,
                            parts=[dict(label='Typical draw', w=f['w'], measured=False)]))
    return out


def initialize(connect):
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS energy_hourly (hour INTEGER, device TEXT, wh REAL, measured_wh REAL, seconds REAL, PRIMARY KEY(hour, device))')


def fetch_pc():
    with urllib.request.urlopen(PC_GPU_URL, timeout=1.5) as response: raw = response.read(8193)
    if len(raw) > 8192: raise ValueError('GPU response too large')
    value = json.loads(raw)
    return value if isinstance(value, dict) and not value.get('error') else None


def collect(connect, hosts, pc_gpu, pc_online, now):
    global _last_tick
    cfg = config()
    devices = readings(hosts or {}, pc_gpu, pc_online, now, cfg)
    dt = now - _last_tick if _last_tick else 0
    _last_tick = now
    if 0 < dt <= MAX_GAP:
        hour = now - now % 3600
        rows = [(hour, d['id'], d['watts'] * dt / 3600, (d['measured_w'] or 0) * dt / 3600, dt) for d in devices if number(d['watts'])]
        with connect() as db:
            db.executemany('INSERT INTO energy_hourly VALUES (?,?,?,?,?) ON CONFLICT(hour, device) DO UPDATE SET '
                           'wh=wh+excluded.wh, measured_wh=measured_wh+excluded.measured_wh, seconds=seconds+excluded.seconds', rows)
            db.execute('DELETE FROM energy_hourly WHERE hour<?', (now - RETENTION,))
    with _lock:
        _latest.clear(); _latest.update(observed_at=now, devices=devices)


def local_day(ts):
    t = time.localtime(ts); return time.strftime('%Y-%m-%d', t)


def snapshot(connect, now=None):
    now = int(now or time.time())
    cfg = config(); rate = cfg.get('rate_per_kwh') if number(cfg.get('rate_per_kwh')) and cfg['rate_per_kwh'] > 0 else None
    cost = lambda kwh: round(kwh * rate, 4) if rate is not None and kwh is not None else None
    lt = time.localtime(now)
    month_start = int(time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1)))
    since = min(month_start, now - 31 * 86400)
    with connect() as db:
        rows = db.execute('SELECT hour, device, wh, measured_wh, seconds FROM energy_hourly WHERE hour>=? ORDER BY hour', (since - 3600,)).fetchall()
        first = db.execute('SELECT MIN(hour) FROM energy_hourly').fetchone()[0]
    days, hours, per_device, month = {}, {}, {}, dict(wh=0.0, seconds={})
    yday, yday_dev, yday_secs = local_day(now - 86400), {}, {}
    for hour, dev, wh, mwh, secs in rows:
        day = local_day(hour)
        d = days.setdefault(day, dict(date=day, wh=0.0, measured_wh=0.0))
        d['wh'] += wh; d['measured_wh'] += mwh
        if hour >= now - 48 * 3600:
            h = hours.setdefault(hour, dict(ts=hour, total_w=0.0))
            if secs > 0: h['total_w'] += wh * 3600 / secs
        if hour >= now - 30 * 86400:
            per_device[dev] = per_device.get(dev, 0.0) + wh
        if day == yday:
            yday_dev[dev] = yday_dev.get(dev, 0.0) + wh; yday_secs[hour] = max(yday_secs.get(hour, 0), secs)
        if hour >= month_start:
            month['wh'] += wh; month['seconds'][hour] = max(month['seconds'].get(hour, 0), secs)
    today = days.get(local_day(now), dict(wh=0.0, measured_wh=0.0))
    covered = sum(month['seconds'].values())
    days_in_month = calendar.monthrange(lt.tm_year, lt.tm_mon)[1]
    projected = month['wh'] / covered * days_in_month * 86400 / 1000 if covered >= 6 * 3600 else None
    with _lock: latest = copy.deepcopy(_latest)
    devs = latest.get('devices') or []
    total = sum(d['watts'] for d in devs if number(d.get('watts'))) if devs else None
    measured = sum(d['measured_w'] or 0 for d in devs if number(d.get('watts'))) if devs else None
    month_kwh = month['wh'] / 1000
    return dict(observed_at=latest.get('observed_at'), devices=devs, total_w=total, measured_w=measured,
                complete=bool(devs) and all(number(d.get('watts')) for d in devs),
                rate_per_kwh=rate, rate_label=cfg.get('rate_label') if rate is not None else None,
                today=dict(kwh=today['wh'] / 1000, cost=cost(today['wh'] / 1000)),
                month=dict(kwh=month_kwh, cost=cost(month_kwh), projected_kwh=projected, projected_cost=cost(projected),
                           covered_hours=covered / 3600, days_in_month=days_in_month),
                per_day_at_now=dict(kwh=total * 24 / 1000, cost=cost(total * 24 / 1000)) if number(total) else None,
                days=[dict(d, kwh=d['wh'] / 1000, cost=cost(d['wh'] / 1000)) for _, d in sorted(days.items()) if d['date'] >= local_day(now - 29 * 86400)],
                hours=[hours[k] for k in sorted(hours)],
                devices_30d=[dict(id=k, kwh=v / 1000, cost=cost(v / 1000)) for k, v in sorted(per_device.items(), key=lambda x: -x[1])],
                yesterday=dict(date=yday, kwh=sum(yday_dev.values()) / 1000, cost=cost(sum(yday_dev.values()) / 1000),
                               covered_hours=sum(yday_secs.values()) / 3600, devices={k: v / 1000 for k, v in yday_dev.items()}) if yday_dev else None,
                history_since=first)
