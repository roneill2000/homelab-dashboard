import ctypes
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

# pc/gpu_metrics.py lives beside the PC, not in the dashboard module path; load it from the file like the deploy copy.
SPEC = importlib.util.spec_from_file_location('gpu_metrics_pc', str(Path(__file__).resolve().parent.parent / 'pc' / 'gpu_metrics.py'))
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)


class FakeLib:
    """Stands in for pdh.dll: open/add/collect succeed, and formatted reads are canned by the stub below."""

    def __init__(self):
        self.collect_status = 0

    def PdhOpenQueryW(self, *args):
        return 0

    def PdhAddEnglishCounterW(self, *args):
        return 0

    def PdhCloseQuery(self, *args):
        return 0

    def PdhCollectQueryData(self, *args):
        return self.collect_status


def reader(energy_raw=0, power_mw=0.0):
    """A Rapl whose formatted counter reads return canned values (Linux cannot load pdh.dll, so _get is overridden)."""

    class Stub(g.Rapl):
        def _get(self, handle, fmt, out):
            if fmt == g._PDH_LARGE:
                out.status = 0
                out.value = energy_raw
            else:
                out.status = 0
                out.value = power_mw
            return True

    r = object.__new__(Stub)
    r.lib = FakeLib()
    r.query = r.energy = r.power = ctypes.c_void_p()
    r.reads = 0
    return r


class RaplTests(unittest.TestCase):
    def test_init_opens_query_and_primes_counters(self):
        with patch.object(g, '_load_pdh', return_value=FakeLib()):
            r = g.Rapl()
        self.assertIsNotNone(r.query)
        self.assertEqual(r.reads, 0)  # the priming collect is not a read

    def test_first_read_gives_energy_but_no_power_average(self):
        r = reader(energy_raw=1000000000, power_mw=11949.0)
        j, w = r.read()
        self.assertAlmostEqual(j, 3.6)
        self.assertIsNone(w)

    def test_second_read_averages_milliwatts(self):
        r = reader(energy_raw=1000000000, power_mw=11949.0)
        r.read()
        j, w = r.read()
        self.assertAlmostEqual(j, 3.6)          # raw x 3.6e-9 -> joules
        self.assertAlmostEqual(w, 11.95)        # the Power counter reports milliwatts

    def test_energy_unit_matches_power_over_a_10s_window(self):
        # The live PC read 12 Nov 2026: raw difference 30,143,556,667 over 10 s and Power 10,850.8 mW
        # (10.85 W) — the exporter's conversions must keep those two agreeing.
        d_joules = 30143556667 * g._ENERGY_JOULES_PER_RAW
        power_w = 10850.77 / 1000
        self.assertLess(abs(d_joules / 10 - power_w) / power_w, 0.05)  # within 5 %
        r = reader(energy_raw=1000000000, power_mw=10850.77)
        r.read()
        j, w = r.read()
        self.assertAlmostEqual(w, 10.85, places=2)
        self.assertAlmostEqual(j, 3.6)

    def test_failed_collection_returns_no_values_not_zeros(self):
        r = reader(energy_raw=1000000000, power_mw=11949.0)
        r.read()
        r.lib.collect_status = 2  # PDH error status
        self.assertEqual(r.read(), (None, None))

    def test_module_loads_without_pdh_on_linux(self):
        self.assertIsNone(g._rapl)  # pdh.dll cannot load here; the endpoint simply omits the cpu fields
        self.assertIsNone(g._nvml)


if __name__ == '__main__':
    unittest.main()
