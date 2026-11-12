"""Tiny read-only GPU + CPU metrics endpoint for the gaming PC (RTX 5070 Ti), for the homelab dashboard.

GET /gpu -> {"name", "power_w", "power_limit_w", "temperature_c", "utilization_pct", "memory_util_pct", "vram_used_mib",
             "vram_total_mib", "fan_pct", "clock_mhz", "observed_at", "cpu_energy_j", "cpu_pkg_w"}
Reads NVIDIA's driver library (nvml.dll) and Windows' own CPU energy counters (PDH API, pdh.dll) directly through
ctypes: microseconds per read, no process launched, nothing runs between requests. The CPU fields come from
\Energy Meter(RAPL_Package0_PKG)\Energy and \Power: cpu_energy_j is the cumulative package energy in joules
(the counter's raw unit is picowatt-hours: joules = raw x 3.6e-9), cpu_pkg_w is the average package watts since
the previous refresh (the Power counter reports milliwatts; null on the first read). Verified 12 Nov 2026 on this
PC: the energy difference over 10 s and the Power average agree to 0.01% (10.85 W both ways). RAPL_Package0_DRAM
reads 0 on this board and is ignored. Reads happen only inside an incoming /gpu request under the 2 s cache: the
dashboard's storage collector fetches this every 10 s around the clock (since 12 Nov 2026) while the Local AI page
pings it every 2 s while open. Falls back to nvidia-smi if NVML can't load; if PDH fails to open or a counter is
missing the cpu fields are omitted and nothing is logged per request. Standard library only.
Run: pythonw gpu_metrics.py  (installed as the "Homelab GPU metrics" startup task; firewall allows 192.168.0.2 only).
"""
import ctypes
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = ('0.0.0.0', 9835)
CACHE_SECONDS = 2.0


class _Memory(ctypes.Structure):
    _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]


class _Util(ctypes.Structure):
    _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]


class Nvml:
    def __init__(self):
        self.lib = ctypes.WinDLL('nvml.dll')
        if self.lib.nvmlInit_v2() != 0:
            raise OSError('nvmlInit failed')
        self.handle = ctypes.c_void_p()
        if self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.handle)) != 0:
            raise OSError('no GPU 0')
        name = ctypes.create_string_buffer(96)
        self.name = name.value.decode() if self.lib.nvmlDeviceGetName(self.handle, name, 96) == 0 else None

    def _uint(self, fn, *args):
        out = ctypes.c_uint()
        return out.value if getattr(self.lib, fn)(self.handle, *args, ctypes.byref(out)) == 0 else None

    def read(self):
        mem, util = _Memory(), _Util()
        mem_ok = self.lib.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(mem)) == 0
        util_ok = self.lib.nvmlDeviceGetUtilizationRates(self.handle, ctypes.byref(util)) == 0
        power = self._uint('nvmlDeviceGetPowerUsage')            # milliwatts
        limit = self._uint('nvmlDeviceGetEnforcedPowerLimit')    # milliwatts
        return {
            'name': self.name,
            'power_w': round(power / 1000, 1) if power is not None else None,
            'power_limit_w': round(limit / 1000) if limit is not None else None,
            'temperature_c': self._uint('nvmlDeviceGetTemperature', ctypes.c_int(0)),
            'utilization_pct': util.gpu if util_ok else None,
            'memory_util_pct': util.memory if util_ok else None,
            'vram_used_mib': round(mem.used / 1048576) if mem_ok else None,
            'vram_total_mib': round(mem.total / 1048576) if mem_ok else None,
            'fan_pct': self._uint('nvmlDeviceGetFanSpeed'),
            'clock_mhz': self._uint('nvmlDeviceGetClockInfo', ctypes.c_int(0)),
        }


def read_smi():
    fields = 'name,power.draw,power.limit,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,fan.speed,clocks.gr'
    out = subprocess.run(['nvidia-smi', f'--query-gpu={fields}', '--format=csv,noheader,nounits'], capture_output=True,
                         text=True, timeout=5, creationflags=0x08000000).stdout.strip().split('\n')[0]
    v = [x.strip() for x in out.split(',')]
    num = lambda x: float(x) if x.replace('.', '', 1).isdigit() else None
    return {'name': v[0], 'power_w': num(v[1]), 'power_limit_w': num(v[2]), 'temperature_c': num(v[3]),
            'utilization_pct': num(v[4]), 'memory_util_pct': num(v[5]), 'vram_used_mib': num(v[6]),
            'vram_total_mib': num(v[7]), 'fan_pct': num(v[8]), 'clock_mhz': num(v[9])}


# PDH return/format values (pdh.h): PDH_FMT_DOUBLE=0x200, PDH_FMT_LARGE=0x400.
_PDH_DOUBLE, _PDH_LARGE = 0x200, 0x400
# Raw unit of \Energy Meter(RAPL_Package0_PKG)\Energy is picowatt-hours -> joules (x 3.6e-9), confirmed 12 Nov 2026
# against the \Power counter on this PC (both windows: 10.85 W, ratio 1.00008).
_ENERGY_JOULES_PER_RAW = 3.6e-9


class _FormattedLarge(ctypes.Structure):
    _fields_ = [('status', ctypes.c_uint32), ('value', ctypes.c_longlong)]


class _FormattedDouble(ctypes.Structure):
    _fields_ = [('status', ctypes.c_uint32), ('value', ctypes.c_double)]


def _load_pdh():
    """The PDH library, or None where it can't load (tests, non-Windows). Separate so tests can stub it."""
    try:
        lib = ctypes.WinDLL('C:/Windows/System32/pdh.dll')
    except (OSError, AttributeError):
        return None
    lib.PdhOpenQueryW.restype = ctypes.c_uint32
    lib.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    lib.PdhAddEnglishCounterW.restype = ctypes.c_uint32
    lib.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    lib.PdhCollectQueryData.restype = ctypes.c_uint32
    lib.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
    lib.PdhGetFormattedCounterValue.restype = ctypes.c_uint32
    lib.PdhGetFormattedCounterValue.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    return lib


class Rapl:
    """Windows' CPU package energy counters through the PDH API, read once per /gpu refresh under the 2 s cache
    (no thread, no timer, no polling loop). Returns (cpu_energy_j, cpu_pkg_w); cpu_pkg_w is null on the first read,
    (None, None) on a failed collection — a never /gpu read that zeros energy."""

    def __init__(self):
        self.lib = _load_pdh()
        if self.lib is None:
            raise OSError('pdh.dll unavailable')
        self.query = ctypes.c_void_p()
        if self.lib.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            self.lib.PdhCloseQuery(self.query)
            raise OSError('PdhOpenQueryW failed')
        self.energy = ctypes.c_void_p()
        if self.lib.PdhAddEnglishCounterW(self.query, r'\Energy Meter(RAPL_Package0_PKG)\Energy', 0,
                                          ctypes.byref(self.energy)) != 0:
            self.lib.PdhCloseQuery(self.query)
            raise OSError('no RAPL package energy counter')
        self.power = ctypes.c_void_p()
        if self.lib.PdhAddEnglishCounterW(self.query, r'\Energy Meter(RAPL_Package0_PKG)\Power', 0,
                                          ctypes.byref(self.power)) != 0:
            self.lib.PdhCloseQuery(self.query)  # closing the query releases both counters
            raise OSError('no RAPL package power counter')
        self.lib.PdhCollectQueryData(self.query)  # first sample; the Power average needs a second collect before it reads
        self.reads = 0

    def read(self):
        if self.lib.PdhCollectQueryData(self.query) not in (0, 1):
            return None, None
        self.reads += 1
        large = _FormattedLarge()
        energy_j = None
        if self._get(self.energy, _PDH_LARGE, large):
            energy_j = round(large.value * _ENERGY_JOULES_PER_RAW, 3)
        pkg_w = None
        if self.reads >= 2:  # first read has no previous collect to average over
            dbl = _FormattedDouble()
            if self._get(self.power, _PDH_DOUBLE, dbl):
                pkg_w = round(dbl.value / 1000.0, 2)  # the counter reports milliwatts
        return energy_j, pkg_w

    def _get(self, handle, fmt, out):
        """One formatted counter read; True when it succeeded and the counter status is valid. Separate so tests
        can supply canned values on Linux, where pdh.dll cannot load."""
        return self.lib.PdhGetFormattedCounterValue(handle, fmt, None, ctypes.byref(out)) == 0 and out.status == 0


_lock = threading.Lock()
_cache = {'at': 0.0, 'body': b'{}'}
try:
    _nvml = Nvml()
except Exception:
    _nvml = None
try:
    _rapl = Rapl()
except Exception:
    _rapl = None


def snapshot():
    with _lock:
        now = time.time()
        if now - _cache['at'] >= CACHE_SECONDS:
            try:
                data = _nvml.read() if _nvml else read_smi()
                data['source'] = 'nvml' if _nvml else 'nvidia-smi'
            except Exception:
                data = {'error': 'GPU reading failed'}
            if _rapl:
                cpu_energy_j, cpu_pkg_w = _rapl.read()
                data['cpu_energy_j'] = cpu_energy_j
                data['cpu_pkg_w'] = cpu_pkg_w
            data['observed_at'] = int(now)
            _cache.update(at=now, body=json.dumps(data).encode())
        return _cache['body']


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != '/gpu':
            self.send_error(404)
            return
        body = snapshot()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer(BIND, Handler).serve_forever()
