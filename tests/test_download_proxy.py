"""Manual download routes; nothing here touches RdtClient or Prowlarr."""
import io
import json
import os
import tempfile
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


class DebridTests(unittest.TestCase):
    """The debrid panel talks to the n100 worker over the JSON interface only: no shell, no ssh -o."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, 'debrid.json')
        self.p = patch.object(dp, 'DEBRID_STATE', self.state_path)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(dp._DEBRID_LOOP.update, {'started': False})

    def write_state(self, rows):
        with open(self.state_path, 'w') as f:
            json.dump({'downloads': rows}, f)

    def read_state(self):
        with open(self.state_path) as f:
            return json.load(f)['downloads']

    def rpc(self, payload=None, answer=None, calls=None):
        """One fake worker: records the payload, answers with `answer`."""
        calls = calls if calls is not None else []

        def call(payload, timeout=None):
            calls.append(dict(payload))
            return answer if answer is not None else {'ok': True, 'unit': 'dl-' + payload.get('name', 'x'),
                                                      'state': 'downloading', 'message': 'started'}
        return call

    # ---------------------------------------------------------------- names ---
    def test_classify_movie_and_show(self):
        m = dp.classify('Memento.2000.BluRay.1080p.mkv')
        self.assertEqual((m['kind'], m['title'], m['season'], m['episode'], m['ext']), ('movie', 'Memento', None, None, '.mkv'))
        s = dp.classify('Breaking.Bad.S05E02.HDTV.x264.mkv')
        self.assertEqual((s['kind'], s['season'], s['episode'], s['show_dir']), ('show', 5, 2, 'Breaking Bad'))

    def test_a_year_in_the_title_survives_the_cleanup(self):
        self.assertEqual(dp.classify('1917.2019.1080p.mkv')['title'], '1917')          # title is a year
        self.assertEqual(dp.classify('2001.A.Space.Odyssey.1968.1080p.mkv')['title'], '2001 A Space Odyssey')
        self.assertEqual(dp.classify('Some.Show.2019.1080p.mkv')['title'], 'Some Show')

    def test_a_season_word_in_a_title_does_not_make_it_a_show(self):
        film = dp.classify('Season.of.the.Witch.2011.720p.mkv')
        self.assertEqual((film['kind'], film['ambiguous']), ('movie', True))
        self.assertIsNone(dp._SHOW_HINT.search('Season of the Witch (2011) horror film'))
        self.assertIsNotNone(dp._SHOW_HINT.search('Severance (TV series, 2022)'))  # a real hint counts

    def test_season_evidence_in_the_name_is_kept(self):
        for name, season, episode in (('Severance.S02E03.BluRay.mkv', 2, 3), ('The.Office.1x04.mkv', 1, 4),
                                      ('Breaking.Bad.Season.5.1080p.mkv', 5, None)):
            info = dp.classify(name)
            self.assertEqual((info['kind'], info['season'], info['episode'], info['ambiguous']),
                             ('show', season, episode, False), name)

    def test_name_from_url(self):
        self.assertEqual(dp._name_from_url('https://nyk.example.com/d/abc/Memento.2000%20BluRay.mkv'),
                         'Memento.2000 BluRay.mkv')

    # ------------------------------------------------------------------ add ---
    def test_add_sends_one_json_start_call_and_remembers_the_row(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, 'searxng_ground', return_value=None):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Memento.2000.BluRay.1080p.mkv')
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]['v'], calls[0]['cmd']), (dp._DEBRID_IF_VERSION, 'start'))
        self.assertEqual((out['kind'], out['dest'], out['name']), ('movie', dp.MOVIES_DIR, 'Memento.2000.BluRay.1080p.mkv'))
        rows = self.read_state()
        self.assertEqual((rows[0]['state'], rows[0]['scanned'], rows[0]['kind']), ('downloading', False, 'movie'))
        self.assertIn('unit', rows[0])

    def test_show_downloads_land_in_the_show_folder(self):
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'searxng_ground', return_value=None):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Breaking.Bad.S05E02.720p.mkv')
        self.assertEqual((out['kind'], out['season'], out['episode']), ('show', 5, 2))
        self.assertEqual(out['dest'], dp.SHOWS_DIR + '/Breaking Bad')

    def test_grounding_is_asked_only_when_the_name_states_nothing(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'searxng_ground', side_effect=lambda t: calls.append(t) or None):
            dp.add_debrid('https://nyk.example.com/d/abc/Breaking.Bad.S05E02.720p.mkv')
            dp.add_debrid('https://nyk.example.com/d/abc/Some.Show.2019.1080p.mkv')
        self.assertEqual(calls, ['Some Show'])

    def test_grounding_can_turn_an_ambiguous_name_into_a_show(self):
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'searxng_ground', return_value='show'):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Some.Show.2019.1080p.mkv')
        self.assertEqual((out['kind'], out['dest'], out['grounded']), ('show', dp.SHOWS_DIR + '/Some Show', 'show'))
        self.assertEqual(self.read_state()[0]['grounded'], 'show')

    def test_a_worker_refusal_becomes_a_clear_error_and_no_row(self):
        def boom(payload, timeout=None):
            raise RuntimeError('this link is not a media file (.mkv, .mp4, ...)')
        with patch.object(dp, '_download_rpc', side_effect=boom), \
             patch.object(dp, 'searxng_ground', return_value=None):
            with self.assertRaises(RuntimeError):
                dp.add_debrid('https://nyk.example.com/d/abc/Memento.2000.mkv')
        self.assertFalse(os.path.exists(self.state_path))

    def test_bad_links_are_refused_before_the_worker_is_asked(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, 'searxng_ground', return_value=None):
            for bad in ('http://nyk.example.com/d/x.mkv', 'not a url', 'https://localhost:8096/x.mkv',
                        'https://192.168.0.13/x.mkv', 'https://nyk.example.com/d/notes.txt',
                        'https://nyk.example.com/d/x.mkv && echo pwned', 'magnet:?xt=urn:btih:abcd'):
                with self.assertRaises(ValueError, msg=bad):
                    dp.add_debrid(bad)
        self.assertEqual(calls, [])

    # ------------------------------------------------------------------ tick ---
    def test_finished_rows_are_scanned_once_and_the_scan_is_recorded(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading', 'bytes': 0}])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'state': 'finished',
                                       'bytes': 4096, 'total_bytes': 4096, 'speed_bytes': 250 * 1024 ** 2,
                                       'eta': ''}]}
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer, calls=calls)), \
             patch.object(dp, '_jellyfin_scan', return_value=True) as scan:
            dp._debrid_tick()
            row = self.read_state()[0]
            dp._debrid_tick()
        self.assertEqual((row['state'], row['bytes'], row['scanned']), ('finished', 4096, True))
        self.assertEqual((row['total_bytes'], row['speed']), (4096, 250 * 1024 ** 2))
        self.assertEqual(scan.call_count, 1)

    def test_a_refused_scan_is_remembered_and_tried_again(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'finished', 'bytes': 10,
                           'scanned': False}])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'state': 'finished', 'bytes': 10}]}
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer)), \
             patch.object(dp, '_jellyfin_scan', return_value=False):
            dp._debrid_tick()
            self.assertEqual(self.read_state()[0]['scan_error'], 'the library scan did not start')
            self.assertFalse(self.read_state()[0]['scanned'])

    def test_every_due_row_travels_in_one_check_call(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'c%d.mkv' % i, 'kind': 'movie', 'title': 'c',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading', 'bytes': 0}
                          for i in range(3)])
        calls = []

        def answer(payload, timeout=None):
            return {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': n, 'state': 'downloading',
                                         'bytes': 100, 'total_bytes': 1000, 'speed_bytes': 7, 'eta': '1m'}
                                        for n in ('c0.mkv', 'c1.mkv', 'c2.mkv')]}
        with patch.object(dp, '_download_rpc', side_effect=answer) as rpc, \
             patch.object(dp, '_jellyfin_scan', return_value=True) as scan:
            dp._debrid_tick()
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(len(rpc.call_args[0][0]['jobs']), 3)
        self.assertEqual(rpc.call_args[0][0]['cmd'], 'check')
        rows = self.read_state()
        self.assertEqual([r['eta'] for r in rows], ['1m', '1m', '1m'])
        scan.assert_not_called()  # nothing finished

    def test_a_row_that_moved_backwards_is_reported_as_it_is(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'finished', 'bytes': 999}])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'state': 'interrupted',
                                        'bytes': 999, 'error': 'the unit was stopped before the file was whole'}]}
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer)), \
             patch.object(dp, '_jellyfin_scan', return_value=True) as scan:
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['scanned']), ('interrupted', False))
        scan.assert_not_called()

    # ---------------------------------------------------------------- status ---
    def test_debrid_status_never_calls_the_worker(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': int(time.time()), 'state': 'downloading',
                           'bytes': 500, 'total_bytes': 1000, 'speed': 7 * 1024 ** 2, 'eta': '12s',
                           'scanned': False}])

        def boom(payload, timeout=None):
            raise AssertionError('a GET request must not reach the worker')
        with patch.object(dp, '_download_rpc', side_effect=boom):
            handled, codes, body = call('/api/debrid', OK, post=False)
        self.assertTrue(handled)
        self.assertEqual(codes, [200])
        row = body['downloads'][0]
        self.assertEqual((row['name'], row['state'], row['done']), ('a.mkv', 'downloading', False))
        self.assertEqual((row['bytes'], row['total_bytes'], row['speed'], row['eta']), (500, 1000, 7 * 1024 ** 2, '12s'))
        self.assertIn('updated', body)

    def test_old_state_from_etc_is_read_then_written_to_the_new_place(self):
        old = os.path.join(self.tmp.name, 'old-debrid.json')
        with open(old, 'w') as f:
            json.dump({'downloads': [{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                                      'season': None, 'episode': None, 'started': 1, 'scanned': True}]}, f)
        with patch.object(dp, 'DEBRID_STATE_OLD', old):
            rows = dp._load_state()['downloads']
        self.assertEqual((rows[0]['state'], rows[0]['unit']), ('unknown', 'dl-a.mkv'))  # old rows get defaults
        with patch.object(dp, 'DEBRID_STATE_OLD', old):
            dp._save_state({'downloads': rows})
        self.assertTrue(os.path.exists(self.state_path))

    def test_state_is_capped_and_written_in_one_step(self):
        rows = [{'dest': dp.MOVIES_DIR, 'name': 'f%02d.mkv' % i, 'kind': 'movie', 'title': 'f',
                 'season': None, 'episode': None, 'started': i, 'state': 'finished', 'scanned': True}
                for i in range(dp._DEBRID_KEEP + 20)]
        with dp._STATE_LOCK:
            dp._save_state({'downloads': rows})
        kept = self.read_state()
        self.assertEqual(len(kept), dp._DEBRID_KEEP)
        self.assertEqual(kept[-1]['name'], 'f69.mkv')  # the newest rows survive
        self.assertEqual(kept[0]['name'], 'f20.mkv')
        self.assertFalse(os.path.exists(self.state_path + '.tmp'))
        out = dp.debrid_status()
        self.assertEqual(len(out['downloads']), dp._DEBRID_ROWS)
        self.assertEqual(out['downloads'][0]['name'], 'f69.mkv')  # newest first

    def test_the_library_scan_runs_with_the_state_lock_free(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading', 'scanned': False}])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'state': 'finished',
                                       'bytes': 999, 'total_bytes': 999}]}

        def scan():
            free = dp._STATE_LOCK.acquire(blocking=False)
            try:
                if not free:
                    raise AssertionError('the library scan ran while the state lock was held')
            finally:
                if free:
                    dp._STATE_LOCK.release()
            return True

        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer)), \
             patch.object(dp, '_jellyfin_scan', side_effect=scan):
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['scanned'], row.get('scan_error')), ('finished', True, None))

    def test_no_network_call_sits_inside_the_state_lock(self):
        # A lock held across a network call freezes the panel and the tick loop for that call's
        # whole timeout (40 s for a stop, 90 s for a batch check). Every such call must run with the
        # lock free, so this is checked against the source, not only against behaviour tests.
        import ast
        path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'download_proxy.py')
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        blocked = {'_download_rpc', '_jellyfin_scan', 'searxng_ground', '_proxmox'}
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if not any(ast.unparse(item.context_expr) == '_STATE_LOCK' for item in node.items):
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and ast.unparse(call.func) in blocked:
                    offenders.append('%s at line %d' % (ast.unparse(call.func), call.lineno))
        self.assertEqual(offenders, [], 'network call inside the state lock: %s' % offenders)

    # -------------------------------------------------------------------- aging and clear ---
    def row(self, name, state, started, state_since=None, scanned=True):
        return {'dest': dp.MOVIES_DIR, 'name': name, 'kind': 'movie', 'title': name,
                'season': None, 'episode': None, 'started': started, 'state': state,
                'bytes': 0, 'scanned': scanned, 'state_since': state_since}

    def test_a_finished_row_older_than_seven_days_leaves_the_list(self):
        old = int(time.time()) - 8 * 86400
        self.write_state([self.row('old.mkv', 'finished', old, old),
                          self.row('recent.mkv', 'finished', int(time.time()) - 3600, int(time.time()) - 3600)])
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer={'ok': True, 'jobs': []})):
            dp._debrid_tick()
        rows = self.read_state()
        self.assertEqual([r['name'] for r in rows], ['recent.mkv'])

    def test_a_running_download_never_ages_out_however_old_it_is(self):
        ancient = int(time.time()) - 40 * 86400
        self.write_state([self.row('still.mkv', 'downloading', ancient, state_since=None, scanned=False)])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'still.mkv', 'state': 'downloading',
                                       'bytes': 10, 'total_bytes': 100}]}
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer)), \
             patch.object(dp, '_jellyfin_scan', return_value=False) as scan:
            dp._debrid_tick()
        self.assertEqual([r['name'] for r in self.read_state()], ['still.mkv'])
        scan.assert_not_called()

    def test_a_row_with_no_timestamp_is_kept(self):
        # No state_since and no started means there is nothing to age against: keep it rather than
        # decide the clock started at zero and drop every old row at once.
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'odd.mkv', 'kind': 'movie', 'title': 'odd',
                           'season': None, 'episode': None, 'state': 'unknown', 'scanned': True}])
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer={'ok': True, 'jobs': []})):
            dp._debrid_tick()
        self.assertEqual([r['name'] for r in self.read_state()], ['odd.mkv'])

    def test_clear_keeps_a_running_download_and_clears_the_rest(self):
        self.write_state([self.row('a.mkv', 'finished', int(time.time()) - 3600),
                          self.row('b.mkv', 'downloading', int(time.time()), scanned=False)])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('clear must not call the worker')):
            handled, codes, body = call('/api/debrid', OK, {'clear': True})
        self.assertEqual((handled, codes, body['cleared'], body['kept']), (True, [200], 1, 1))
        self.assertEqual([r['name'] for r in self.read_state()], ['b.mkv'])
        self.assertTrue(body['message'][0].isupper())

    def test_clear_empties_the_rows_without_touching_the_worker(self):
        self.write_state([self.row('a.mkv', 'finished', int(time.time()) - 86400),
                          self.row('b.mkv', 'stopped', int(time.time()) - 7200)])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('clear must not call the worker')):
            handled, codes, body = call('/api/debrid', OK, {'clear': True})
        self.assertEqual((handled, codes, body['ok'], body['cleared']), (True, [200], True, 2))
        self.assertEqual(self.read_state(), [])
        self.assertEqual(dp.debrid_status()['downloads'], [])

    def test_clear_keeps_a_finished_row_whose_library_scan_has_not_run(self):
        # The state file is the scan queue: clearing an unscanned finished row would mean Jellyfin
        # is never asked about that file.
        self.write_state([self.row('done.mkv', 'finished', int(time.time()) - 600, scanned=False),
                          self.row('old.mkv', 'finished', int(time.time()) - 86400)])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('clear must not call the worker')):
            handled, codes, body = call('/api/debrid', OK, {'clear': True})
        self.assertEqual((handled, codes, body['cleared'], body['kept']), (True, [200], 1, 1))
        self.assertEqual([r['name'] for r in self.read_state()], ['done.mkv'])
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer={'ok': True, 'jobs': []})), \
             patch.object(dp, '_jellyfin_scan', return_value=True) as scan:
            dp._debrid_tick()
        scan.assert_called_once()
        self.assertTrue(self.read_state()[0]['scanned'])

    def test_clear_needs_the_action_header(self):
        self.write_state([self.row('a.mkv', 'finished', int(time.time()))])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('must not reach the worker')):
            self.assertEqual(call('/api/debrid', {'Host': '192.168.0.2'}, {'clear': True})[1], [403])

    # ------------------------------------------------------------------ stop ---

    def test_stop_goes_through_the_worker_and_closes_the_row(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading', 'scanned': False}])
        calls = []
        answer = {'ok': True, 'unit': 'dl-a.mkv', 'name': 'a.mkv', 'state': 'stopped', 'message': 'stopped dl-a.mkv'}
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer, calls=calls)):
            handled, codes, body = call('/api/debrid', OK, {'stop': 'a.mkv'})
        self.assertTrue(handled)
        self.assertEqual(codes, [200])
        self.assertEqual((calls[0]['cmd'], calls[0]['name']), ('stop', 'a.mkv'))
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['scanned']), ('stopped', True))  # stopped on purpose: never scanned

    def test_stop_needs_the_action_header_and_a_name_in_the_list(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading'}])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('must not reach the worker')):
            self.assertEqual(call('/api/debrid', {'Host': '192.168.0.2'}, {'stop': 'a.mkv'})[1], [403])
            self.assertEqual(call('/api/debrid', OK, {'stop': 'not-there.mkv'})[1], [400])
            self.assertEqual(call('/api/debrid', OK, {'stop': '../escape.mk'})[1], [400])
            self.assertEqual(call('/api/debrid', OK, {'stop': 42})[1], [400])
            self.assertEqual(call('/api/debrid', OK, {})[1], [400])

    def test_a_worker_refusal_while_stopping_is_reported(self):
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading'}])

        def boom(payload, timeout=None):
            raise RuntimeError('there is no download with that file name to stop')
        with patch.object(dp, '_download_rpc', side_effect=boom):
            handled, codes, body = call('/api/debrid', OK, {'stop': 'a.mkv'})
        self.assertEqual(codes, [503])
        self.assertIn('no download', body['error'])

    def test_a_stop_holds_the_state_lock_only_around_the_rows(self):
        # One Stop waits up to 40 s on the worker. The panel read and the tick loop must not be
        # blocked for that long, so the lock must be free while the worker call runs. threading.Lock
        # is not reentrant, so a failed non-blocking acquire from this thread proves it was held.
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading'}])

        def probe(payload, timeout=None):
            free = dp._STATE_LOCK.acquire(blocking=False)
            try:
                if not free:
                    raise AssertionError('the state lock was held during the worker call')
            finally:
                if free:
                    dp._STATE_LOCK.release()
            return {'ok': True, 'unit': 'dl-a.mkv', 'name': 'a.mkv', 'state': 'stopped',
                    'message': 'stopped dl-a.mkv'}

        with patch.object(dp, '_download_rpc', side_effect=probe):
            handled, codes, body = call('/api/debrid', OK, {'stop': 'a.mkv'})
        self.assertEqual((handled, codes), (True, [200]))
        self.assertEqual((self.read_state()[0]['state'], self.read_state()[0]['scanned']), ('stopped', True))

    def test_a_row_that_left_the_list_during_the_stop_is_reported(self):
        # The tick loop or another Stop can rewrite the state while this Stop waits on the worker.
        # The row is looked up again before it is written, so a vanished row is refused, not re-added.
        self.write_state([{'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a',
                           'season': None, 'episode': None, 'started': 1, 'state': 'downloading'}])

        def concurrent(payload, timeout=None):
            self.write_state([])          # the row is gone by the time the worker answers
            return {'ok': True, 'unit': 'dl-a.mkv', 'name': 'a.mkv', 'state': 'stopped'}

        with patch.object(dp, '_download_rpc', side_effect=concurrent):
            handled, codes, body = call('/api/debrid', OK, {'stop': 'a.mkv'})
        self.assertEqual(codes, [400])
        self.assertIn('left the list', body['error'])
        self.assertEqual(self.read_state(), [])

    # ---------------------------------------------------------------- routes ---
    def test_debrid_get_route_answers_from_the_state_file(self):
        self.write_state([])
        handled, codes, body = call('/api/debrid', OK, post=False)
        self.assertTrue(handled)
        self.assertEqual((codes, body['ok'], body['downloads']), ([200], True, []))

    def test_url_route_still_adds_a_debrid_download(self):
        url = 'https://nyk.example.com/d/abc/x.mkv'
        with patch.object(dp, 'add_debrid', return_value={'ok': True, 'kind': 'movie', 'title': 'x', 'season': None,
                                                          'episode': None, 'dest': dp.MOVIES_DIR, 'name': 'x.mkv',
                                                          'grounded': None, 'started': 'started dl-x'}) as add:
            self.assertEqual(call('/api/action/download', OK, {'url': url})[1], [200])
        add.assert_called_once_with(url)

    def test_url_route_rejects_non_url(self):
        with patch.object(dp, 'add_debrid') as add:
            self.assertEqual(call('/api/action/download', OK, {'url': 'not a url'})[1], [400])
        add.assert_not_called()

    def test_the_loop_only_starts_where_the_download_key_exists(self):
        with patch('os.path.exists', return_value=False):
            self.assertFalse(dp.start_debrid_loop())

    def test_other_paths_still_fall_through(self):
        self.assertFalse(call('/api/debrid/other', OK, post=False)[0])
        self.assertFalse(call('/api/other', OK, post=True)[0])
