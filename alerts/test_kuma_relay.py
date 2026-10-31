import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import kuma_ntfy_relay as k


def beat(status, name='Proxmox', mid=1, msg='timeout'):
    return {'heartbeat': {'status': status, 'msg': msg}, 'monitor': {'id': mid, 'name': name}}


class KumaRelayTests(unittest.TestCase):
    def setUp(self):
        k.held.clear(); k.snooze_until = 0.0
        self.sent = []
        self.send = lambda h, b: self.sent.append((h, b)) or True

    def test_down_opens_its_page_with_buttons_and_up_replaces_it(self):
        self.assertEqual(k.handle(beat(0), now=100, send=self.send), 'sent')
        h, body = self.sent[0]
        self.assertEqual((h['Click'], h['X-Sequence-ID'], h['Priority'], body), ('homelab://view/hosts', 'kuma-1', '5', 'timeout'))
        self.assertIn('Ask AI', h['Actions']); self.assertIn('Snooze 2 h', h['Actions'])
        k.handle(beat(1), now=200, send=self.send)
        up = self.sent[1][0]
        self.assertEqual(up['X-Sequence-ID'], 'kuma-1'); self.assertNotIn('Actions', up); self.assertTrue(up['Title'].startswith('RECOVERED'))

    def test_pages_and_non_events(self):
        self.assertEqual([k.page_for(n) for n in ('Unraid Share', 'SMB', 'slideaway.duckdns.org', 'Unraid UI')],
                         ['storage', 'storage', 'network', 'hosts'])
        self.assertEqual(k.handle({'heartbeat': {'status': 2}, 'monitor': {'name': 'x'}}, send=self.send), 'ignored')  # pending
        self.assertEqual(k.handle({'msg': 'test'}, send=self.send), 'ignored')
        self.assertEqual(self.sent, [])

    def test_snooze_holds_downs_drops_their_recovery_and_sends_the_rest_after(self):
        k.snooze_until = 1000
        self.assertEqual(k.handle(beat(0, 'Proxmox', 1), now=100, send=self.send), 'held')
        self.assertEqual(k.handle(beat(0, 'Unraid', 3), now=110, send=self.send), 'held')
        self.assertEqual(k.handle(beat(1, 'Proxmox', 1), now=300, send=self.send), 'dropped')  # back before the snooze ended
        self.assertEqual(k.flush_held(now=900, send=self.send), 0)
        self.assertEqual(k.flush_held(now=1001, send=self.send), 1)  # Unraid is still down: alert now, once
        self.assertEqual([h['X-Sequence-ID'] for h, _ in self.sent], ['kuma-3'])
        self.assertEqual(k.flush_held(now=1100, send=self.send), 0)

    def test_unreachable_dashboard_keeps_the_last_known_snooze(self):
        k.snooze_until = 5000
        def boom(*a, **kw): raise OSError('Proxmox rebooting')
        k.refresh_snooze(fetch=boom)
        self.assertEqual(k.snooze_until, 5000)


if __name__ == '__main__':
    unittest.main()
