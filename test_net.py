import unittest
import net_monitor as n

class NetTests(unittest.TestCase):
    def test_rates_and_resets(self):
        self.assertEqual(n.rates({'rx': 0, 'tx': 0}, {'rx': 1000, 'tx': 500}, 5), (1600.0, 800.0))
        self.assertEqual(n.rates({'rx': 1000, 'tx': 500}, {'rx': 10, 'tx': 600}, 5), (None, 160.0))
        self.assertEqual(n.rates(None, {'rx': 1, 'tx': 1}, 5), (None, None))
        self.assertEqual(n.rates({'rx': 0, 'tx': 0}, {'rx': 1, 'tx': 1}, 120), (None, None))

if __name__ == '__main__':
    unittest.main()
