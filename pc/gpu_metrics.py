"""Tiny read-only GPU metrics endpoint for the gaming PC (RTX 5070 Ti), for the homelab dashboard's Local AI page.

GET /gpu -> {"name", "power_w", "power_limit_w", "temperature_c", "utilization_pct", "memory_util_pct", "vram_used_mib",
             "vram_total_mib", "fan_pct", "clock_mhz", "observed_at"}
Reads NVIDIA's driver library (nvml.dll) directly through ctypes: microseconds per read, no process launched, nothing
runs between requests. Falls back to nvidia-smi if NVML can't load. Results are cached for 2 s, so repeated polling
costs nothing extra. The dashboard only asks while its Local AI page is open. Standard library only.
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


_lock = threading.Lock()
_cache = {'at': 0.0, 'body': b'{}'}
try:
    _nvml = Nvml()
except Exception:
    _nvml = None


def snapshot():
    with _lock:
        now = time.time()
        if now - _cache['at'] >= CACHE_SECONDS:
            try:
                data = _nvml.read() if _nvml else read_smi()
                data['source'] = 'nvml' if _nvml else 'nvidia-smi'
            except Exception:
                data = {'error': 'GPU reading failed'}
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
