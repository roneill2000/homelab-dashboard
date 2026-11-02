import tempfile
import unittest
from pathlib import Path

import maintenance as m


class MaintenanceTests(unittest.TestCase):
    def test_snooze_choices_expiry_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'm.json'
            self.assertFalse(m.status(1000, path)['active'])  # no file: not snoozed
            s = m.set_minutes(30, 1000, path)
            self.assertTrue(s['active']); self.assertEqual(s['until'], 1000 + 1800)
            self.assertTrue(m.status(2799, path)['active'])
            self.assertFalse(m.status(2800, path)['active']); self.assertIsNone(m.status(2800, path)['until'])
            m.set_minutes(120, 3000, path); self.assertFalse(m.set_minutes(0, 3001, path)['active'])
            for bad in (5, -30, 10000):
                with self.assertRaises(ValueError): m.set_minutes(bad, 1000, path)

    def test_corrupt_state_is_not_snoozed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'm.json'
            for text in ('not json', '[1,2]', '{"until": true}', '{"until": "9999999999"}'):
                path.write_text(text); self.assertFalse(m.status(1000, path)['active'])


if __name__ == '__main__':
    unittest.main()
