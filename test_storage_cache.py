"""In-memory /api/storage snapshot cache; no database or network."""
import json
import unittest
from unittest.mock import patch

import storage_monitor as sm


class StorageCacheTests(unittest.TestCase):
    def setUp(self):
        sm._snap.clear(); sm._wanted.clear(); sm._wanted[3600] = 0.0

    def test_serves_from_memory_until_old_then_rebuilds(self):
        calls = []
        with patch.object(sm, 'snapshot', side_effect=lambda w: calls.append(w) or {'w': w, 'n': len(calls)}):
            first = sm.cached_snapshot(3600)
            self.assertEqual(json.loads(first), {'w': 3600, 'n': 1})
            self.assertIs(sm.cached_snapshot(3600), first)          # fresh: no rebuild
            sm._snap[3600] = (sm._snap[3600][0] - 60, first)
            self.assertEqual(json.loads(sm.cached_snapshot(3600))['n'], 2)
        self.assertEqual(calls, [3600, 3600])

    def test_warm_keeps_default_and_recent_windows_only(self):
        with patch.object(sm, 'snapshot', side_effect=lambda w: {'w': w}):
            sm.cached_snapshot(86400)                                # asked for just now
            sm._wanted[21600] = 0.0; sm._snap[21600] = (0, b'{}')     # asked long ago
            sm._warm()
        self.assertEqual(sorted(sm._snap), [3600, 86400])

    def test_window_is_clamped(self):
        with patch.object(sm, 'snapshot', side_effect=lambda w: {'w': w}):
            self.assertEqual(json.loads(sm.cached_snapshot(5))['w'], 900)


if __name__ == '__main__':
    unittest.main()
