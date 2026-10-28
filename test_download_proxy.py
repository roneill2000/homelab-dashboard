"""Manual download routes; nothing here touches RdtClient or Prowlarr."""
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import download_proxy as dp

OK = {'Host': '192.168.0.2', 'X-Dashboard-Action': '1'}
MAGNET = 'magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny'


def call(path, headers, body=None, post=True):
    codes, out = [], io.BytesIO()
    raw = json.dumps(body).encode() if body is not None else b''
    h = SimpleNamespace(path=path, headers={'Content-Length': str(len(raw)), **headers}, rfile=io.BytesIO(raw), wfile=out,
                        send_response=codes.append, send_header=lambda *a: None, end_headers=lambda: None)
    handled = dp.handle(h, post=post)
    return handled, codes, json.loads(out.getvalue() or b'{}')


class DownloadTests(unittest.TestCase):
    def test_other_paths_fall_through(self):
        self.assertFalse(call('/api/updates', OK, post=False)[0])
        self.assertFalse(call('/api/action/download', OK, post=False)[0])

    def test_action_needs_dashboard_header(self):
        with patch.object(dp, 'add_magnet') as add:
            _, codes, _ = call('/api/action/download', {'Host': '192.168.0.2'}, {'magnet': MAGNET})
        self.assertEqual(codes, [403]); add.assert_not_called()

    def test_magnet_validated_then_sent(self):
        with patch.object(dp, 'add_magnet') as add:
            for bad in ('http://example.com/x.torrent', 'magnet:?dn=no-hash', MAGNET + '\nX-Injected: 1'):
                self.assertEqual(call('/api/action/download', OK, {'magnet': bad})[1], [400], bad)
            add.assert_not_called()
            self.assertEqual(call('/api/action/download', OK, {'magnet': '  ' + MAGNET + ' '})[1], [200])
        add.assert_called_once_with(MAGNET)

    def test_grab_needs_a_live_token(self):
        dp.RESULTS.clear()
        dp.RESULTS['abc'] = {'at': time.time(), 'guid': 'g', 'indexerId': 6, 'title': 'T'}
        with patch.object(dp, 'prowlarr') as pr:
            self.assertEqual(call('/api/action/download', OK, {'result': 'nope'})[1], [410])
            _, codes, body = call('/api/action/download', OK, {'result': 'abc'})
        self.assertEqual((codes, body['title']), ([200], 'T'))
        pr.assert_called_once_with('/api/v1/search', {'guid': 'g', 'indexerId': 6})

    def test_search_hides_indexer_urls_and_filters_protocol(self):
        rows = [{'guid': 'https://idx/1', 'indexerId': 1, 'title': 'A', 'protocol': 'torrent', 'seeders': 5,
                 'downloadUrl': 'http://127.0.0.1:9696/1/download?apikey=secret', 'magnetUrl': 'magnet:?x'},
                {'guid': 'https://idx/2', 'indexerId': 2, 'title': 'B', 'protocol': 'usenet', 'grabs': 9}]
        with patch.object(dp, 'prowlarr', return_value=rows):
            _, codes, body = call('/api/downloads/search?q=ab&kind=torrent', OK, post=False)
        self.assertEqual(codes, [200]); self.assertEqual([r['title'] for r in body['results']], ['A'])
        self.assertNotIn('secret', json.dumps(body)); self.assertNotIn('idx/1', json.dumps(body))
        self.assertEqual(call('/api/downloads/search?q=a', OK, post=False)[1], [400])

    def test_recent_uses_real_stage_progress(self):
        qbit = [{'name': 'A', 'hash': 'AA', 'progress': 0.69, 'added_on': 2, 'state': 'downloading'},
                {'name': 'B', 'hash': 'bb', 'progress': 0.1, 'added_on': 1, 'state': 'metaDL'},
                {'name': 'C', 'hash': 'cc', 'progress': 1, 'added_on': 0, 'state': 'pausedUP'}]
        native = [{'hash': 'aa', 'torrentId': 't1', 'rdProgress': 100, 'statusText': 'Downloading file 1/1'},
                  {'hash': 'bb', 'torrentId': 't2', 'rdProgress': 20, 'rdSpeed': 5, 'rdSeeders': 0}]
        pages = {'/api/v2/torrents/info': qbit, '/Api/Torrents': native,
                 '/Api/Torrents/Get/t1': {'downloads': [{'bytesTotal': 100, 'bytesDone': 38, 'speed': 7}]}}
        with patch.object(dp, 'rdt', side_effect=lambda path, data=None: json.dumps(pages[path]).encode()):
            a, b, c = dp.recent()
        self.assertEqual((a['stage'], a['progress'], a['dlspeed']), ('local', 0.38, 7))
        self.assertEqual((b['stage'], b['progress'], b['seeders']), ('debrid', 0.2, 0))
        self.assertEqual((c.get('stage'), c['progress']), (None, 1))
        pages['/Api/Torrents/Get/t1'] = {'downloads': [{'bytesTotal': 100, 'bytesDone': 100}]}
        with patch.object(dp, 'rdt', side_effect=lambda path, data=None: json.dumps(pages[path]).encode()):
            self.assertEqual(dp.recent()[0]['stage'], 'finishing')


if __name__ == '__main__':
    unittest.main()
