"""Manual download routes; nothing here touches RdtClient or Prowlarr."""
import base64
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import download_proxy as dp

OK = {'Host': '192.168.0.2', 'X-Dashboard-Action': '1'}
MAGNET = 'magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny'


class _FakeResponse:
    """Stand-in for one HTTP answer: headers only, since the name probe reads nothing but headers."""
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False



_SAFE_KEYS = []


def setUpModule():
    """A test must never read a real key: point both key files at paths that do not exist, and start
    each cache clean, whatever ran before this module in the same process."""
    for const, path in (('TORBOX', '/nonexistent-homelab-torbox.json'),
                        ('REALDEBRID', '/nonexistent-homelab-realdebrid.json')):
        p = patch.object(dp, const, path)
        p.start()
        _SAFE_KEYS.append(p)
    dp.TORBOX_KEY = None
    dp.TORBOX_KEY_AT = 0
    dp.RD_KEY = None
    dp.RD_KEY_AT = 0


def tearDownModule():
    for p in _SAFE_KEYS:
        p.stop()


def call(path, headers, body=None, post=True):
    codes, out = [], io.BytesIO()
    raw = json.dumps(body).encode() if body is not None else b''
    h = SimpleNamespace(path=path, headers={'Content-Length': str(len(raw)), **headers}, rfile=io.BytesIO(raw), wfile=out,
                        send_response=codes.append, send_header=lambda *a: None, end_headers=lambda: None)
    handled = dp.handle(h, post=post)
    return handled, codes, json.loads(out.getvalue() or b'{}')


class DownloadTests(unittest.TestCase):
    def setUp(self):
        """This class is the no-key behaviour: make both key files absent and both caches cold, whatever
        an earlier class left in them."""
        for const in ('TORBOX', 'REALDEBRID'):
            p = patch.object(dp, const, '/nonexistent-homelab-%s.json' % const.lower())
            p.start()
            self.addCleanup(p.stop)
        dp.TORBOX_KEY = None
        dp.TORBOX_KEY_AT = 0
        dp.RD_KEY = None
        dp.RD_KEY_AT = 0

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

    # ------------------------------------------------------------- dismiss ---
    HASH40 = '0123456789abcdef0123456789abcdef01234567'

    def rdtfake(self, pages):
        """Fake RdtClient: answers known paths, records every call (path, data)."""
        calls = []
        def call(path, data=None):
            calls.append((path, data))
            return json.dumps(pages.get(path, [])).encode()
        return calls, call

    def test_recent_rows_carry_the_lowercase_hash_as_id(self):
        qbit = [{'name': 'A', 'hash': self.HASH40.upper(), 'category': 'prowlarr', 'state': 'error',
                 'progress': 1, 'added_on': 2},
                {'name': 'B', 'hash': '', 'category': 'prowlarr', 'state': 'metaDL', 'progress': 1, 'added_on': 1}]
        calls, f = self.rdtfake({'/api/v2/torrents/info': qbit})
        with patch.object(dp, 'rdt', side_effect=f):
            _, codes, body = call('/api/downloads', OK, post=False)
        self.assertEqual(codes, [200])
        self.assertEqual(calls, [('/api/v2/torrents/info', None)])   # nothing active, no native follow-up
        a, b = body['items']
        self.assertEqual(a['id'], self.HASH40)   # the panel can address the errored row
        self.assertNotIn('hash', a)
        self.assertNotIn('id', b)                # no hash: no id, no Dismiss button

    def test_dismiss_of_an_errored_prowlarr_grab_deletes_the_list_entry_only(self):
        info = [{'name': 'A', 'hash': self.HASH40, 'category': 'prowlarr', 'state': 'error', 'added_on': 1}]
        calls, f = self.rdtfake({'/api/v2/torrents/info': info})
        with patch.object(dp, 'rdt', side_effect=f):
            handled, codes, body = call('/api/downloads', OK, {'dismiss': self.HASH40})
        self.assertEqual((handled, codes, body), (True, [200], {'ok': True, 'id': self.HASH40}))
        deletes = [d for p, d in calls if p == '/api/v2/torrents/delete']
        self.assertEqual(deletes, [{'hashes': self.HASH40, 'deleteFiles': 'false'}])  # exactly one, no data

    def test_dismiss_accepts_an_item_only_RdtClient_own_record_calls_error(self):
        info = [{'name': 'A', 'hash': self.HASH40, 'category': 'prowlarr', 'state': 'queued', 'added_on': 1}]
        native = [{'hash': self.HASH40, 'error': 'the indexer refused the file'}]
        calls, f = self.rdtfake({'/api/v2/torrents/info': info, '/Api/Torrents': native})
        with patch.object(dp, 'rdt', side_effect=f):
            self.assertEqual(call('/api/downloads', OK, {'dismiss': self.HASH40})[1], [200])
        self.assertEqual([d for p, d in calls if p == '/api/v2/torrents/delete'],
                         [{'hashes': self.HASH40, 'deleteFiles': 'false'}])

    def test_dismiss_refuses_healthy_items_other_categories_and_unknown_ids(self):
        info = [{'name': 'A', 'hash': self.HASH40, 'category': 'prowlarr', 'state': 'downloading', 'added_on': 1},
                {'name': 'B', 'hash': 'ab' * 32, 'category': 'manual', 'state': 'error', 'added_on': 2}]
        calls, f = self.rdtfake({'/api/v2/torrents/info': info})
        with patch.object(dp, 'rdt', side_effect=f):
            for bad in (self.HASH40,          # healthy prowlarr item (native list empty)
                        'ab' * 32,             # errored but not a search grab
                        self.HASH40.replace('0', 'g', 1),   # malformed id
                        'ff' * 20,             # well formed, not in the list
                        'short', ''):
                self.assertEqual(call('/api/downloads', OK, {'dismiss': bad})[1], [400], bad)
        self.assertNotIn('/api/v2/torrents/delete', [p for p, _ in calls])

    def test_malformed_ids_never_reach_rdtclient_and_dismiss_needs_the_header(self):
        calls, f = self.rdtfake({'/api/v2/torrents/info': []})
        with patch.object(dp, 'rdt', side_effect=f):
            for bad in ('ZZ' * 20, 'x', 'g' * 40):
                self.assertEqual(call('/api/downloads', OK, {'dismiss': bad})[1], [400], bad)
        self.assertEqual(calls, [])
        with patch.object(dp, 'rdt', side_effect=AssertionError('must not reach RdtClient')):
            self.assertEqual(call('/api/downloads', {'Host': '192.168.0.2'}, {'dismiss': 'ff' * 20})[1], [403])
            self.assertEqual(call('/api/downloads', OK, {'dismiss': 42})[1], [400])
            self.assertEqual(call('/api/downloads', OK, {})[1], [400])

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
        # Space and size readings are cached in this process: a stale one would answer for another test.
        self.addCleanup(dp._SPACE_CACHE.clear)
        self.addCleanup(dp._SIZE_CACHE.clear)
        self.addCleanup(setattr, dp, '_SPACE_SUPPORT', [0.0, False])
        # Both service keys are cached for 60 s once read, so a key one test fakes would otherwise leak into
        # the next one and turn on a key-gated feature ('search', 'cached') it never asked for.
        self.addCleanup(setattr, dp, 'RD_KEY', None)
        self.addCleanup(setattr, dp, 'RD_KEY_AT', 0)
        self.addCleanup(setattr, dp, 'TORBOX_KEY', None)
        self.addCleanup(setattr, dp, 'TORBOX_KEY_AT', 0)

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

    def test_release_tags_are_not_episode_evidence(self):
        # 16x9 / 21x9 / 16x10 are aspect ratios and E-9 / E_07 can be a release tag. What sits after the
        # tech tags (2160p, x264, BluRay) is a tag, not an episode, so the name stays ambiguous.
        for name in ('300.2006.2160p.16x9.mkv', 'Some.Movie.2019.2160p.21x9.mkv', 'Some.Movie.2019.16x10.mkv',
                     'Reservoir.Dogs.1992.1080p.E-9.mkv', 'Some.Movie.2020.x264.1080p.E_07.mkv'):
            info = dp.classify(name)
            self.assertEqual((info['kind'], info['ambiguous'], info['season'], info['episode']),
                             ('movie', True, None, None), name)

    def test_an_episode_marker_before_the_tech_tags_still_counts(self):
        for name, season, episode in (('The.Office.1x04.1080p.mkv', 1, 4), ('Some.Show.E-12.720p.mkv', 1, 12),
                                      ('Some.Show.Episode.7.1080p.mkv', 1, 7), ('30.Rock.S01E01.mkv', 1, 1)):
            info = dp.classify(name)
            self.assertEqual((info['kind'], info['season'], info['episode'], info['ambiguous']),
                             ('show', season, episode, False), name)

    def test_the_title_keeps_no_release_tag_and_the_year_is_kept_separate(self):
        self.assertEqual(dp.classify('Severance.S02.2022.BluRay.1080p.mkv')['title'], 'Severance')
        self.assertEqual(dp.classify('Some.Show.2019.1080p.1x04.mkv')['title'], 'Some Show')
        self.assertEqual(dp.classify('300.2006.2160p.16x9.mkv')['title'], '300')
        self.assertEqual(dp.classify('Dune.2021.2160p.BluRay.mkv')['year'], 2021)
        self.assertIsNone(dp.classify('Memento.BluRay.1080p.mkv')['year'])

    # ------------------------------------------------------------ grounding ---
    def ground(self, title, year, results):
        with patch.object(dp, 'searxng_results', return_value=results):
            return dp.searxng_ground(title, year)

    def test_a_tie_in_the_search_answers_nothing(self):
        # One side may not win a tie: the old rule sent every tie to 'show', which is how Dune (2021)
        # ended up in Shows. No answer is better than a coin flip.
        results = ['Other page one - a site it is a television series about a hospital',
                   'Other page two - a site an american film about a hospital',
                   'Other page three - a site another television series',
                   'Other page four - a site another american film']
        self.assertIsNone(self.ground('Some Title', 2019, results))

    def test_the_film_page_for_the_year_in_the_name_decides(self):
        results = ['Dune (franchise) - Wikipedia the franchise has films and a television series',
                   'Dune (2021 film) - Wikipedia Dune is a 2021 american film directed by Denis Villeneuve',
                   'Dune (novel) - Wikipedia a 1965 science fiction novel']
        self.assertEqual(self.ground('Dune', 2021, results), 'movie')

    def test_the_series_page_that_states_the_year_decides(self):
        results = ['Andor (American TV series) - Wikipedia an american television series that premiered in 2022']
        self.assertEqual(self.ground('Andor', 2022, results), 'show')

    def test_a_page_about_a_different_work_with_the_same_name_is_dropped(self):
        # Dallas names both a long-running series and a 2017 film: the series page must not decide
        # a file named Dallas.2017, and only when a page speaks about that year may it answer.
        results = ['Dallas (TV series) - Wikipedia an american television drama series that aired from 1978 to 1991',
                   'Dallas (2017 film) - Wikipedia a 2017 american film']
        self.assertEqual(self.ground('Dallas', 2017, results), 'movie')

    def test_a_show_that_was_a_film_first_stays_a_show(self):
        results = ['Some Show - Wikipedia an american television series based on the 2019 film of the same name',
                   'Some Show season 2 premiered in 2024 - a site',
                   'Some Show - Wikipedia a television series with two seasons']
        self.assertEqual(self.ground('Some Show', None, results), 'show')

    def test_a_year_only_title_is_not_cut_in_half(self):
        # '2012' with no release year in the name: the title itself is the year.
        self.assertEqual(dp.classify('2012.2160p.BluRay.mkv')['title'], '2012')
        self.assertIsNone(dp.classify('2012.2160p.BluRay.mkv')['year'])

    # ------------------------------------------------------------- the library ---
    def test_the_library_hint_reads_the_title_out_of_jellyfin(self):
        with patch.object(dp, '_jellyfin_items', return_value=[{'Name': 'Dune', 'Type': 'Movie'}]):
            self.assertEqual(dp.library_hint('Dune'), 'movie')
        with patch.object(dp, '_jellyfin_items', return_value=[{'Name': 'The Crown', 'Type': 'Series'}]):
            self.assertEqual(dp.library_hint('The Crown'), 'show')
        with patch.object(dp, '_jellyfin_items', return_value=[{'Name': 'Dune', 'Type': 'Movie'},
                                                               {'Name': 'Dune', 'Type': 'Series'}]):
            self.assertIsNone(dp.library_hint('Dune'))                 # owned both ways: answers nothing
        with patch.object(dp, '_jellyfin_items', return_value=[{'Name': 'Dune Part Two', 'Type': 'Movie'}]):
            self.assertIsNone(dp.library_hint('Dune'))                 # a similar title is not evidence
        with patch.object(dp, '_jellyfin_items', return_value=None):
            self.assertIsNone(dp.library_hint('Dune'))                 # Jellyfin did not answer

    def test_an_episode_guess_uses_no_network_at_all(self):
        # No header in this test process means no Jellyfin call, so grounding is the only lookup made.
        self.assertIsNone(dp.library_hint('Dune'))

    def test_name_from_url(self):
        self.assertEqual(dp._name_from_url('https://nyk.example.com/d/abc/Memento.2000%20BluRay.mkv'),
                         'Memento.2000 BluRay.mkv')

    # ------------------------------------------------------------------ add ---
    def test_add_sends_one_json_start_call_and_remembers_the_row(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, 'searxng_ground', return_value=None):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Memento.2000.BluRay.1080p.mkv')
        # The size is unknown for this fake link, so the guard asked nothing; the room reading that
        # follows the start is the only space call. One start, as before (Task 1 adds a read-only op).
        self.assertEqual(sorted(c['cmd'] for c in calls), ['space', 'start'])
        self.assertEqual(len([c for c in calls if c['cmd'] == 'start']), 1)
        self.assertEqual((next(c for c in calls if c['cmd'] == 'start')['v'],), (dp._DEBRID_IF_VERSION,))
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
             patch.object(dp, 'searxng_ground', side_effect=lambda t, y=None: calls.append((t, y)) or None):
            dp.add_debrid('https://nyk.example.com/d/abc/Breaking.Bad.S05E02.720p.mkv')
            dp.add_debrid('https://nyk.example.com/d/abc/Some.Show.2019.1080p.mkv')
        self.assertEqual(calls, [('Some Show', 2019)])        # only the ambiguous name, with its year

    def test_the_library_is_asked_before_the_search_engine(self):
        asked = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'library_hint', return_value='show'), \
             patch.object(dp, 'searxng_ground', side_effect=lambda *a: asked.append(a) or 'movie'):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Some.Show.2019.1080p.mkv')
        self.assertEqual((out['kind'], out['dest'], out['grounded'], out['kind_source']),
                         ('show', dp.SHOWS_DIR + '/Some Show', 'show', 'library'))
        self.assertEqual(asked, [])                                  # the search engine was not asked

    def test_a_name_that_states_an_episode_asks_nobody(self):
        asked = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'library_hint', side_effect=lambda *a: asked.append(a) or None), \
             patch.object(dp, 'searxng_ground', side_effect=lambda *a: asked.append(a) or None):
            out = dp.add_debrid('https://nyk.example.com/d/abc/Breaking.Bad.S05E02.720p.mkv')
        self.assertEqual((out['kind'], out['kind_source']), ('show', 'name'))
        self.assertEqual(asked, [])
        self.assertEqual(self.read_state()[0]['kind_source'], 'name')

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

    # ---------------------------------------------------------------- preview ---
    def test_preview_says_the_folder_without_touching_the_worker_or_the_state(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)):
            _, codes, body = call('/api/action/download', OK,
                                   {'url': 'https://nyk.example.com/d/abc/Game.Of.Thrones.S01E03.2160p.mkv', 'preview': True})
        self.assertEqual(codes, [200])
        self.assertIs(body['preview'], True)   # the panel treats any answer without this as a failed check
        self.assertEqual((body['kind'], body['dest'], body['name'], body['kind_source']),
                         ('show', dp.SHOWS_DIR + '/Game Of Thrones', 'Game.Of.Thrones.S01E03.2160p.mkv', 'name'))
        self.assertEqual((body['season'], body['episode'], body['year']), (1, 3, None))
        # Room (Task 1): a preview may read free space from the worker, but must not start anything.
        self.assertEqual([c['cmd'] for c in calls], ['space'])
        self.assertIsNone(body['free_bytes'])   # this fake worker said no size: the check fails open
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_user_kind_beats_an_unambiguous_name(self):
        _, codes, body = call('/api/action/download', OK,
                              {'url': 'https://nyk.example.com/d/abc/Breaking.Bad.S05E02.720p.mkv',
                               'preview': True, 'kind': 'movie'})
        self.assertEqual(codes, [200])
        self.assertEqual((body['kind'], body['dest'], body['kind_source']), ('movie', dp.MOVIES_DIR, 'user'))

    def test_a_user_kind_beats_an_ambiguous_name_and_skips_every_lookup(self):
        asked = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'library_hint', side_effect=lambda *a: asked.append(a) or None), \
             patch.object(dp, 'searxng_ground', side_effect=lambda *a: asked.append(a) or None):
            _, codes, body = call('/api/action/download', OK,
                                  {'url': 'https://nyk.example.com/d/abc/Some.Show.2019.1080p.mkv',
                                   'preview': True, 'kind': 'movie'})
        self.assertEqual(codes, [200])
        self.assertEqual((body['kind'], body['dest'], body['kind_source']), ('movie', dp.MOVIES_DIR, 'user'))
        self.assertEqual(asked, [])   # no library, no search engine

    def test_a_start_with_a_user_kind_records_it(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)):
            _, codes, body = call('/api/action/download', OK,
                                  {'url': 'https://nyk.example.com/d/abc/Dune.2021.2160p.mkv', 'kind': 'show'})
        self.assertEqual(codes, [200])
        self.assertEqual((body['kind'], body['dest'], body['kind_source']), ('show', dp.SHOWS_DIR + '/Dune', 'user'))
        self.assertEqual(calls[0]['dest'], dp.SHOWS_DIR + '/Dune')
        rows = self.read_state()
        self.assertEqual((rows[0]['kind'], rows[0]['kind_source']), ('show', 'user'))

    def test_a_bad_kind_is_refused_before_anything_runs(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)):
            for kind in ('film', 'SHOW', 'show '):
                for body in ({'url': 'https://nyk.example.com/d/abc/Memento.2000.mkv', 'preview': True, 'kind': kind},
                             {'url': 'https://nyk.example.com/d/abc/Memento.2000.mkv', 'kind': kind}):
                    self.assertEqual(call('/api/action/download', OK, body)[1], [400], str(body))
        self.assertEqual(calls, [])

    def test_preview_of_a_link_that_names_no_media_file_is_a_400(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, '_discover_name',
                          side_effect=ValueError('the server did not say what the file is called')):
            handled, codes, body = call('/api/action/download', OK,
                                        {'url': 'https://torbox.example/dld/4fb4e907?token=t', 'preview': True})
        self.assertEqual(codes, [400])
        self.assertIn('does not name a media file', body['error'])
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_debrid_status_offers_the_preview_and_retry_features(self):
        self.write_state([])
        with patch.object(dp, '_download_rpc', side_effect=lambda *a, **k: self.fail('the worker must not be asked')):
            _, codes, body = call('/api/debrid', OK, post=False)
        self.assertEqual(codes, [200])
        # 'search' and 'cached' ride with the key files, so only the always-on ones are checked here
        self.assertEqual([f for f in body['features'] if f not in ('search', 'cached')], ['preview', 'retry', 'dismiss'])

    # ------------------------------------------------- name discovery (TorBox) ---
    def test_a_name_in_the_headers_is_read_out_of_content_disposition(self):
        cases = {
            'attachment; filename="Dune.2021.2160p.mkv"': 'Dune.2021.2160p.mkv',
            "attachment; filename*=UTF-8''Dune.2021.mkv": 'Dune.2021.mkv',
            "attachment; filename*=UTF-8''Some%20Show.S01E01.mkv": 'Some Show.S01E01.mkv',
            'attachment; filename="x.mkv"; filename*=UTF-8\'\'real.mkv': 'real.mkv',
            'attachment; filename=Dune.mkv': 'Dune.mkv',
            'inline': None, '': None, 'attachment; filename="notes.txt"': None,
            'attachment; filename="../../escape.mkv"': None, 'attachment; filename=".hidden.mkv"': None,
            'attachment; filename="-x.mkv"': None, 'attachment; filename=".mkv"': None,
        }
        for header, want in cases.items():
            self.assertEqual(dp._media_name(dp._header_name({'Content-Disposition': header})), want, header)

    def test_the_probe_is_one_byte_and_follows_only_safe_redirects(self):
        hops = []

        def open_(req, timeout=None):
            hops.append((req.full_url, req.headers.get('Range')))
            if len(hops) == 1:
                raise urllib.error.HTTPError(req.full_url, 302, 'Found', {'Location': 'https://cdn.example/a/b'}, None)
            return _FakeResponse({'Content-Disposition': 'filename="Dune.2021.mkv"'})

        with patch.object(dp, '_probe_opener', return_value=SimpleNamespace(open=open_)):
            self.assertEqual(dp._discover_name('https://nexus-228.example/dld/4fb4e907?token=t'), 'Dune.2021.mkv')
        self.assertEqual([h[1] for h in hops], ['bytes=0-0', 'bytes=0-0'])
        self.assertEqual(hops[1][0], 'https://cdn.example/a/b')

    def test_a_probe_redirect_to_the_lan_is_refused(self):
        def open_(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 302, 'Found', {'Location': 'https://192.168.0.2/x.mkv'}, None)
        with patch.object(dp, '_probe_opener', return_value=SimpleNamespace(open=open_)):
            with self.assertRaises(ValueError):
                dp._discover_name('https://cdn.example/dld/4fb4e907')

    def test_a_probe_that_answers_nothing_or_refuses_is_a_clear_error(self):
        def open_(req, timeout=None):
            return _FakeResponse({})
        with patch.object(dp, '_probe_opener', return_value=SimpleNamespace(open=open_)):
            with self.assertRaises(ValueError):
                dp._discover_name('https://cdn.example/dld/4fb4e907')

        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, None)
        with patch.object(dp, '_probe_opener', return_value=SimpleNamespace(open=refuse)):
            with self.assertRaises(ValueError):
                dp._discover_name('https://cdn.example/dld/4fb4e907')

        def dead(req, timeout=None):
            raise urllib.error.URLError('no route')
        with patch.object(dp, '_probe_opener', return_value=SimpleNamespace(open=dead)):
            with self.assertRaises(ValueError):
                dp._discover_name('https://cdn.example/dld/4fb4e907')

    def test_an_id_only_link_starts_with_the_name_the_server_named(self):
        calls, probes = [], []

        def probe(url):
            probes.append(url)
            return 'Dune.2021.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv'
        url = 'https://nexus-228.example/dld/4fb4e907-d23c-4705-bfdb-9b5d0723efec?token=2559c10a'
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, 'searxng_ground', return_value=None), \
             patch.object(dp, '_discover_name', side_effect=probe):
            out = dp.add_debrid(url)
        self.assertTrue(out['ok'])
        self.assertEqual(probes, [url])                      # asked the server, once
        self.assertEqual(calls[0]['name'], out['name'])
        self.assertEqual((out['kind'], out['title'], out['dest']),
                         ('movie', 'Dune', dp.MOVIES_DIR))
        self.assertEqual(out['name'], 'Dune.2021.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv')
        self.assertEqual(self.read_state()[0]['name'], out['name'])

    def test_a_link_the_server_will_not_name_is_refused_before_the_worker_is_asked(self):
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(calls=calls)), \
             patch.object(dp, '_discover_name', side_effect=ValueError('the server did not say what the file is called')):
            with self.assertRaises(ValueError) as got:
                dp.add_debrid('https://cdn.example/dld/4fb4e907?token=t')
        self.assertIn('does not name a media file', str(got.exception))
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_named_link_never_probes_the_server(self):
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, '_discover_name', side_effect=AssertionError('probed a link that already named the file')):
            dp.add_debrid('https://nyk.example.com/d/abc/Memento.2000.mkv')

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

    def test_the_state_file_is_private_before_its_first_byte(self):
        """These rows hold pasted links with their access tokens: the tmp file must already be 0600
        when the first byte lands in it, not chmodded once the whole write is done."""
        seen = []
        real_open = open

        def spy_open(path, mode='r', *a, **kw):
            f = real_open(path, mode, *a, **kw)
            if not str(path).endswith('.tmp'):
                return f

            class _Spy:
                def __init__(self):
                    self._f = f
                def write(self, s):
                    seen.append(os.stat(path).st_mode & 0o777)   # the mode at the first byte written
                    return self._f.write(s)
                def __enter__(self):
                    return self
                def __exit__(self, *exc):
                    return self._f.__exit__(*exc)
                def __getattr__(self, name):
                    return getattr(self._f, name)
            return _Spy()

        row = {'dest': dp.MOVIES_DIR, 'name': 'a.mkv', 'kind': 'movie', 'title': 'a', 'season': None,
               'episode': None, 'started': 1, 'state': 'downloading', 'scanned': False,
               'url': 'https://download.invalid/f.mkv?token=S3CRET'}
        with patch.object(dp, 'open', spy_open, create=True):
            with dp._STATE_LOCK:
                dp._save_state({'downloads': [row]})
        self.assertTrue(seen, 'the tmp file was never written')
        self.assertEqual(sorted(set(seen)), [0o600])
        self.assertEqual(os.stat(self.state_path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(self.state_path + '.tmp'))

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
        blocked = {'_download_rpc', '_jellyfin_scan', 'searxng_ground', '_proxmox', 'library_hint', '_jellyfin_items'}
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

    # ------------------------------------------------------------------- retry ---
    def linked_row(self, name, state, **over):
        row = {'dest': dp.MOVIES_DIR, 'name': name, 'kind': 'movie', 'title': name,
               'season': None, 'episode': None, 'started': 1, 'state': state, 'scanned': True,
               'error': None, 'url': 'https://cdn.example.com/d/abc/' + name + '?token=abc123'}
        row.update(over)
        return row

    def test_add_keeps_the_link_but_the_panel_never_sees_it(self):
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()), \
             patch.object(dp, 'searxng_ground', return_value=None):
            dp.add_debrid('https://nyk.example.com/d/abc/a.mkv?token=t1')
        row = self.read_state()[0]
        self.assertEqual(row['url'], 'https://nyk.example.com/d/abc/a.mkv?token=t1')
        for sent in dp.debrid_status()['downloads']:
            self.assertNotIn('url', sent)
            self.assertNotIn('token', json.dumps(sent))
            self.assertIs(sent['can_retry'], False)   # the row is downloading

    def test_the_state_file_is_written_private_and_an_old_one_is_fixed_on_load(self):
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()):
            dp.add_debrid('https://nyk.example.com/d/abc/a.mkv?token=t1')
        self.assertEqual(oct(os.stat(self.state_path).st_mode & 0o777), '0o600')
        os.chmod(self.state_path, 0o644)               # a file from before this rule
        dp.debrid_status()
        self.assertEqual(oct(os.stat(self.state_path).st_mode & 0o777), '0o600')

    def test_can_retry_is_true_only_for_the_three_states_with_a_stored_link(self):
        self.write_state([self.linked_row('dl.mkv', 'downloading'),
                          self.linked_row('fin.mkv', 'finished'),
                          self.linked_row('bad.mkv', 'failed'),
                          self.linked_row('st.mkv', 'stopped'),
                          self.linked_row('int.mkv', 'interrupted'),
                          dict(self.linked_row('old.mkv', 'failed'), url=None)])
        _, codes, body = call('/api/debrid', OK, post=False)
        self.assertEqual(codes, [200])
        self.assertEqual({r['name']: r['can_retry'] for r in body['downloads']},
                         {'dl.mkv': False, 'fin.mkv': False, 'bad.mkv': True, 'st.mkv': True,
                          'int.mkv': True, 'old.mkv': False})
        for sent in body['downloads']:
            self.assertNotIn('url', sent)

    def test_retry_refuses_rows_that_are_missing_unlinkable_or_not_retriable(self):
        self.write_state([self.linked_row('run.mkv', 'downloading'), self.linked_row('fin.mkv', 'finished'),
                          dict(self.linked_row('old.mkv', 'failed'), url=None)])
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('must not reach the worker')):
            for name in ('not-there.mkv', 'run.mkv', 'fin.mkv', 'old.mkv', '../escape.mk', 42):
                self.assertEqual(call('/api/debrid', OK, {'retry': name})[1], [400], str(name))
            self.assertEqual(call('/api/debrid', {'Host': '192.168.0.2'}, {'retry': 'bad.mkv'})[1], [403])

    def test_retry_restarts_with_the_stored_link_and_holds_no_lock(self):
        self.write_state([self.linked_row('a.mkv', 'failed')])
        calls = []

        def probe(payload, timeout=None):
            free = dp._STATE_LOCK.acquire(blocking=False)
            try:
                if not free:
                    raise AssertionError('the state lock was held during the worker call')
            finally:
                if free:
                    dp._STATE_LOCK.release()
            calls.append(dict(payload))
            return {'ok': True, 'unit': 'dl-a.mkv', 'state': 'downloading', 'message': 'started a.mkv'}

        with patch.object(dp, '_download_rpc', side_effect=probe):
            handled, codes, body = call('/api/debrid', OK, {'retry': 'a.mkv'})
        self.assertEqual((handled, codes, body['state']), (True, [200], 'downloading'))
        self.assertEqual((calls[0]['cmd'], calls[0]['url'], calls[0]['dest'], calls[0]['name']),
                         ('start', 'https://cdn.example.com/d/abc/a.mkv?token=abc123', dp.MOVIES_DIR, 'a.mkv'))
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['error'], row['scanned'], row['scan_error'], row['retried']),
                         ('downloading', None, False, None, True))
        self.assertGreater(row['started'], 1_000_000_000)
        self.assertEqual(oct(os.stat(self.state_path).st_mode & 0o777), '0o600')

    def test_a_stopped_row_can_be_retried_and_reopens_the_scan_queue(self):
        # stop_debrid marks the row scanned (a partial file is never scanned); a retry must reset that.
        self.write_state([self.linked_row('a.mkv', 'stopped', scanned=True, error='stopped')])
        with patch.object(dp, '_download_rpc', side_effect=self.rpc()):
            self.assertEqual(call('/api/debrid', OK, {'retry': 'a.mkv'})[1], [200])
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['scanned'], row['retried']), ('downloading', False, True))

    def test_an_expired_link_after_a_retry_closes_the_row(self):
        # aria2c 22/24 after a retry means the access token in the stored link has run out: keep the row
        # failed, say so plainly, drop the link (so can_retry goes false). A first attempt that fails with
        # 22 keeps its raw error and its link; a non-retried row is untouched by this rule.
        self.write_state([self.linked_row('bad.mkv', 'failed', retried=True, error='aria2c exited with code 4'),
                          self.linked_row('first.mkv', 'failed')])
        answer = {'ok': True, 'jobs': [{'dest': dp.MOVIES_DIR, 'name': 'bad.mkv', 'state': 'failed',
                                        'error': 'aria2c exited with code 22 (no file was written)'},
                                       {'dest': dp.MOVIES_DIR, 'name': 'first.mkv', 'state': 'failed',
                                        'error': 'aria2c exited with code 22'}]}
        with patch.object(dp, '_download_rpc', side_effect=self.rpc(answer=answer)), \
             patch.object(dp, '_jellyfin_scan', return_value=False):
            dp._debrid_tick()
        rows = {r['name']: r for r in self.read_state()}
        self.assertEqual(rows['bad.mkv']['error'], 'the link has expired; paste a fresh one')
        self.assertIsNone(rows['bad.mkv']['url'])
        self.assertIs(rows['bad.mkv']['retried'], False)
        self.assertEqual(rows['first.mkv']['error'], 'aria2c exited with code 22')
        self.assertEqual(rows['first.mkv']['url'], 'https://cdn.example.com/d/abc/first.mkv?token=abc123')
        self.assertEqual({r['name']: r['can_retry'] for r in dp.debrid_status()['downloads']},
                         {'bad.mkv': False, 'first.mkv': True})

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
        add.assert_called_once_with(url, None)

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


class DebridGrabTests(DebridTests):
    """Flow step 1, as revised: a Debrid-tab Get adds the torrent at Real-Debrid, or at TorBox
    when Real-Debrid will not do it, and adds a 'preparing' row. Fakes only, except where a
    real HTTP answer is the thing being checked."""

    KEY = '2559c10a-d8ab-4e42-b7fe-a86195c9edd8'
    RD_KEY = 'rd-abc123DEF456ghi789'
    HASH = 'dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c'
    MAGNET = 'magnet:?xt=urn:btih:' + HASH + '&dn=Big+Buck+Bunny'

    def setUp(self):
        super().setUp()
        self.torbox_path = os.path.join(self.tmp.name, 'torbox.json')
        self.rd_path = os.path.join(self.tmp.name, 'realdebrid.json')
        with open(self.torbox_path, 'w') as f:
            json.dump({'key': self.KEY}, f)
        with open(self.rd_path, 'w') as f:
            json.dump({'key': self.RD_KEY}, f)
        self.p_tb = patch.object(dp, 'TORBOX', self.torbox_path)
        self.p_rd = patch.object(dp, 'REALDEBRID', self.rd_path)
        self.p_tb.start()
        self.p_rd.start()
        self.addCleanup(self.p_tb.stop)
        self.addCleanup(self.p_rd.stop)
        self.addCleanup(dp.RESULTS.clear)

    def reset_key_cache(self):
        dp.TORBOX_KEY = None
        dp.TORBOX_KEY_AT = 0
        dp.RD_KEY = None
        dp.RD_KEY_AT = 0

    def only_torbox_key(self):
        os.remove(self.rd_path)
        self.reset_key_cache()

    def no_keys(self):
        os.remove(self.rd_path)
        os.remove(self.torbox_path)
        self.reset_key_cache()

    def seed(self, token, magnet=None, download=None, info_hash=HASH, title='Big Buck Bunny 2008 720p',
             protocol='torrent'):
        dp.RESULTS[token] = {'at': time.time(), 'guid': 'g', 'indexerId': 6, 'title': title,
                             'magnetUrl': magnet, 'downloadUrl': download, 'infoHash': info_hash,
                             'protocol': protocol}

    def fake_torbox(self, answer=None, calls=None, error=None):
        calls = calls if calls is not None else []

        def call(method, path, fields=(), files=(), timeout=20):
            calls.append({'method': method, 'path': path, 'fields': dict(fields), 'files': [f[0] for f in files]})
            if error is not None:
                raise error
            return answer if answer is not None else {'success': True, 'data': {'torrent_id': 123, 'hash': self.HASH}}
        return call

    def fake_rd(self, answer=None, calls=None, error=None):
        calls = calls if calls is not None else []

        def call(method, path, fields=None, raw=None, timeout=20):
            calls.append({'method': method, 'path': path, 'fields': fields, 'raw': raw})
            if error is not None:
                raise error
            return answer if answer is not None else {'id': 42, 'hash': self.HASH}
        return call

    def indexer_fake(self, body=b'', code=200, location='', url=None):
        """One fake answer from _indexer_get: (code, Location, body) for one download URL."""
        def get(url_):
            assert url_ == url, ('asked for the wrong URL', url_)
            return code, location, body
        return get

    # --------------------------------------------------------------- off ---
    def test_no_key_get_goes_to_prowlarr_and_search_is_not_a_feature(self):
        self.no_keys()
        self.seed('t1')
        with patch.object(dp, 'prowlarr') as pr, \
             patch.object(dp, 'torbox', side_effect=AssertionError('TorBox must not be called')), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('Real-Debrid must not be called')):
            _, codes, body = call('/api/action/download', OK, {'result': 't1'})
            _, codes2, body2 = call('/api/debrid', OK, post=False)
        self.assertEqual((codes, body['title']), ([200], 'Big Buck Bunny 2008 720p'))
        pr.assert_called_once_with('/api/v1/search', {'guid': 'g', 'indexerId': 6})
        self.assertNotIn('search', body2['features'])
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_usenet_get_goes_to_prowlarr_even_with_both_keys(self):
        """An NZB is not a torrent: a Usenet result keeps the Prowlarr -> SABnzbd path when keys exist."""
        self.seed('u1', download='http://192.168.0.13:9696/1/download?apikey=x', info_hash=None, protocol='usenet')
        with patch.object(dp, 'prowlarr') as pr, \
             patch.object(dp, '_indexer_get', side_effect=AssertionError('an NZB must not be fetched as a torrent')), \
             patch.object(dp, 'torbox', side_effect=AssertionError('TorBox must not be called')), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('Real-Debrid must not be called')):
            _, codes, body = call('/api/action/download', OK, {'result': 'u1'})
        self.assertEqual(codes, [200])
        self.assertNotEqual(body.get('via'), 'debrid')
        pr.assert_called_once_with('/api/v1/search', {'guid': 'g', 'indexerId': 6})
        self.assertFalse(os.path.exists(self.state_path))

    def test_no_key_get_reports_a_refused_grab_as_a_503(self):
        """The old path still answers with its own error text instead of throwing out of the route."""
        self.no_keys()
        self.seed('t1b')
        with patch.object(dp, 'grab', side_effect=RuntimeError('RdtClient is not answering')), \
             patch.object(dp, 'torbox', side_effect=AssertionError('no key: the debrid path must not run')), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('no key: the debrid path must not run')):
            _, codes, body = call('/api/action/download', OK, {'result': 't1b'})
        self.assertEqual((codes, body['error']), ([503], 'Prowlarr could not send it to the downloader.'))
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_key_present_offers_search_to_the_panel(self):
        # With a service to send a torrent to, the Debrid tab and its Cancel button appear; with none, Get
        # keeps its old Prowlarr path and they stay away (checked in the no-key test above).
        self.reset_key_cache()
        self.seed('t9', magnet=self.MAGNET)
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd()), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox()):
            call('/api/action/download', OK, {'result': 't9'})
            _, codes, body = call('/api/debrid', OK, post=False)
        # Both key files are present here, so the Ready now check ('cached', Task 3) is offered as well:
        # it is key-gated for the same reason - with no TorBox to ask, the mark could only ever be unknown.
        self.assertEqual(body['features'], ['preview', 'retry', 'dismiss', 'search', 'cached'])

    # --------------------------------------------------------------- which provider ---
    def test_only_torbox_key_get_goes_straight_to_torbox(self):
        self.only_torbox_key()
        self.seed('t2', magnet=self.MAGNET)
        tb, rd = [], []
        with patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')):
            _, codes, body = call('/api/action/download', OK, {'result': 't2'})
        self.assertEqual((codes, body['ok'], body['via']), ([200], True, 'debrid'))
        self.assertEqual(len(tb), 1)
        self.assertEqual(rd, [])
        row = self.read_state()[0]
        self.assertEqual((row['name'], row['state'], row['provider']), ('torbox-123', 'preparing', 'torbox'))
        self.assertEqual(row['tried'], ['torbox'])
        self.assertEqual((row['debrid']['id'], row['debrid']['hash']), (123, self.HASH))
        self.assertEqual(row['prep']['provider'], 'TorBox')
        self.assertIsNone(row['error'])

    def test_only_realdebrid_key_get_goes_straight_to_realdebrid(self):
        """A Real-Debrid key on its own is enough: the Get takes the debrid path, not RdtClient's.
        The panel's 'search' feature and this route must agree, or the Debrid tab would be a lie."""
        os.remove(self.torbox_path)          # only the Real-Debrid key file is left
        self.reset_key_cache()
        self.seed('t2b', magnet=self.MAGNET)
        tb, rd = [], []
        with patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')):
            _, codes, body = call('/api/action/download', OK, {'result': 't2b'})
            _, _, status = call('/api/debrid', OK, post=False)
        self.assertEqual((codes, body['ok'], body['via']), ([200], True, 'debrid'))
        self.assertEqual([(c['method'], c['path']) for c in rd], [('POST', '/torrents/addMagnet')])
        self.assertEqual(tb, [])
        self.assertIn('search', status['features'])
        row = self.read_state()[0]
        self.assertEqual((row['name'], row['state'], row['provider']), ('rd-42', 'preparing', 'realdebrid'))
        self.assertEqual(row['tried'], ['realdebrid'])

    def test_both_keys_get_uses_realdebrid_only(self):
        self.reset_key_cache()
        self.seed('t3', magnet=self.MAGNET)
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')):
            _, codes, body = call('/api/action/download', OK, {'result': 't3'})
        self.assertEqual(codes, [200])
        self.assertEqual(len(rd), 1)
        self.assertEqual((rd[0]['method'], rd[0]['path']), ('POST', '/torrents/addMagnet'))
        self.assertEqual(rd[0]['fields'], {'magnet': self.MAGNET})
        self.assertEqual(tb, [])
        row = self.read_state()[0]
        self.assertEqual((row['name'], row['state'], row['provider']), ('rd-42', 'preparing', 'realdebrid'))
        self.assertEqual(row['tried'], ['realdebrid'])
        self.assertEqual((row['debrid']['id'], row['debrid']['hash']), (42, self.HASH))
        self.assertEqual(row['prep'], {'progress': 0, 'seeds': None, 'files': None, 'provider': 'Real-Debrid'})
        self.assertEqual(row['title'], 'Big Buck Bunny')
        self.assertEqual(row['release'], 'Big Buck Bunny 2008 720p')
        self.assertIsNone(row['kind'])
        self.assertIsNone(row['dest'])
        self.assertEqual((row['bytes'], row['scanned']), (0, False))
        self.assertGreater(row['started'], 1_000_000_000)

    def test_a_torrent_file_result_uploads_the_bytes_to_realdebrid(self):
        self.reset_key_cache()
        self.seed('t4', download='http://192.168.0.13:9696/1/download?apikey=secret')
        torrent_bytes = b'd8:announce12:tracker.com4:info' + b'x' * 40
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')), \
             patch.object(dp, '_indexer_get',
                          side_effect=self.indexer_fake(body=torrent_bytes,
                                                        url='http://192.168.0.13:9696/1/download?apikey=secret')):
            _, codes, _ = call('/api/action/download', OK, {'result': 't4'})
        self.assertEqual(codes, [200])
        self.assertEqual((rd[0]['method'], rd[0]['path']), ('PUT', '/torrents/addTorrent'))
        self.assertEqual(rd[0]['raw'], torrent_bytes)
        self.assertEqual(rd[0]['fields'], None)
        self.assertEqual(tb, [])
        self.assertEqual(len(self.read_state()), 1)

    def test_a_realdebrid_refusal_of_this_torrent_goes_to_torbox(self):
        self.reset_key_cache()
        self.seed('t5', magnet=self.MAGNET)
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd, error=dp.RDRefusal('infringing file'))), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')):
            _, codes, body = call('/api/action/download', OK, {'result': 't5'})
        self.assertEqual((codes, body['ok'], body['via']), ([200], True, 'debrid'))
        self.assertEqual(len(rd), 1)
        self.assertEqual(len(tb), 1)
        row = self.read_state()[0]
        self.assertEqual((row['name'], row['provider'], row['tried']), ('torbox-123', 'torbox', ['realdebrid', 'torbox']))
        self.assertIn('Real-Debrid', row['error'])
        self.assertIn('infringing file', row['error'])

    def test_realdebrid_account_trouble_still_uses_torbox_for_this_grab(self):
        self.reset_key_cache()
        self.seed('t6', magnet=self.MAGNET)
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd, error=dp.RDAccountError('the Real-Debrid key is not accepted'))), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)):
            _, codes, _ = call('/api/action/download', OK, {'result': 't6'})
        self.assertEqual(codes, [200])
        self.assertEqual((len(rd), len(tb)), (1, 1))
        row = self.read_state()[0]
        self.assertEqual(row['provider'], 'torbox')
        self.assertIn('key is not accepted', row['error'])

    def test_both_services_refusing_is_a_503_naming_both(self):
        self.reset_key_cache()
        self.seed('t7', magnet=self.MAGNET)
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(error=dp.RDRefusal('infringing file'))), \
             patch.object(dp, 'torbox', side_effect=RuntimeError('TorBox says: file blocked')):
            _, codes, body = call('/api/action/download', OK, {'result': 't7'})
        self.assertEqual(codes, [503])
        self.assertIn('Real-Debrid: infringing file', body['error'])
        self.assertIn('TorBox says: file blocked', body['error'])
        self.assertFalse(os.path.exists(self.state_path))

    def test_the_error_text_never_carries_a_key(self):
        self.reset_key_cache()
        self.seed('t8', magnet=self.MAGNET)
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(error=dp.RDRefusal('blocked by ' + self.RD_KEY))), \
             patch.object(dp, 'torbox', side_effect=RuntimeError('blocked by ' + self.KEY)):
            _, codes, body = call('/api/action/download', OK, {'result': 't8'})
        self.assertEqual(codes, [503])
        self.assertNotIn(self.RD_KEY, body['error'])
        self.assertNotIn(self.KEY, body['error'])
        self.assertIn('***', body['error'])

    # --------------------------------------------------------------- the torrent itself ---
    def test_a_real_indexer_redirect_to_a_magnet_is_that_magnet(self):
        """No mocks of the HTTP layer: a real HTTP 302 to a magnet: must not be followed."""
        self.reset_key_cache()
        magnet = self.MAGNET

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', magnet)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(('127.0.0.1', 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.handle_request)
        t.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(t.join, 5)
        self.seed('tr', download='http://127.0.0.1:%d/7/download?apikey=secret' % port)
        rd = []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=AssertionError('Real-Debrid must win first')):
            _, codes, body = call('/api/action/download', OK, {'result': 'tr'})
        self.assertEqual(codes, [200])
        self.assertEqual(rd[0]['fields'], {'magnet': magnet})
        self.assertEqual(rd[0]['raw'], None)

    def test_an_unusable_download_url_is_a_503_and_adds_nothing(self):
        self.reset_key_cache()
        self.seed('t9', download='http://192.168.0.13:9696/3/download?apikey=secret')
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, '_indexer_get',
                          side_effect=self.indexer_fake(body=b'this is not a torrent',
                                                        url='http://192.168.0.13:9696/3/download?apikey=secret')):
            _, codes, body = call('/api/action/download', OK, {'result': 't9'})
        self.assertEqual(codes, [503])
        self.assertIn('usable torrent', body['error'])
        self.assertEqual((rd, tb), ([], []))
        self.assertFalse(os.path.exists(self.state_path))

    def test_a_torrent_file_over_the_size_cap_is_refused(self):
        self.reset_key_cache()
        self.seed('tc', download='http://192.168.0.13:9696/9/download?apikey=secret')
        big = b'd8:announce4:info' + b'x' * dp._TORBOX_MAX_TORRENT_BYTES
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, '_indexer_get',
                          side_effect=self.indexer_fake(body=big[:dp._TORBOX_MAX_TORRENT_BYTES + 1],
                                                        url='http://192.168.0.13:9696/9/download?apikey=secret')):
            _, codes, body = call('/api/action/download', OK, {'result': 'tc'})
        self.assertEqual(codes, [503])
        self.assertEqual((rd, tb), ([], []))
        self.assertFalse(os.path.exists(self.state_path))

    # --------------------------------------------------------------- no double adds ---
    def test_a_second_get_for_the_same_hash_adds_nothing(self):
        self.reset_key_cache()
        self.seed('t6a', magnet=self.MAGNET)
        self.seed('t6b', magnet=self.MAGNET)   # the same torrent, a new search token
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)), \
             patch.object(dp, 'prowlarr', side_effect=AssertionError('Prowlarr must not be asked to grab')):
            _, codes1, _ = call('/api/action/download', OK, {'result': 't6a'})
            _, codes2, body2 = call('/api/action/download', OK, {'result': 't6b'})
        self.assertEqual((codes1, codes2), ([200], [200]))
        self.assertEqual((body2['ok'], body2['via'], body2['title']), (True, 'debrid', 'Big Buck Bunny'))
        self.assertEqual((len(rd), len(tb)), (1, 0))
        self.assertEqual(len(self.read_state()), 1)

    def test_an_old_row_written_with_a_torbox_object_still_blocks_a_second_get(self):
        """Rows step 1 wrote before this revision kept a torbox object; the dedup still sees them."""
        self.write_state([{'name': 'torbox-7', 'state': 'preparing', 'title': 'Big Buck Bunny', 'kind': None,
                           'torbox': {'torrent_id': 7, 'hash': self.HASH}, 'dest': None, 'bytes': 0,
                           'scanned': False}])
        self.reset_key_cache()
        self.seed('t10', magnet=self.MAGNET)
        rd = []
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd(calls=rd)), \
             patch.object(dp, 'torbox', side_effect=AssertionError('TorBox must not be called again')):
            _, codes, body = call('/api/action/download', OK, {'result': 't10'})
        self.assertEqual(codes, [200])
        self.assertEqual((body['ok'], body['title']), (True, 'Big Buck Bunny'))
        self.assertEqual(rd, [])
        self.assertEqual(len(self.read_state()), 1)

    # --------------------------------------------------------------- no leaks ---
    def test_nothing_sensitive_is_in_any_answer(self):
        self.reset_key_cache()
        self.seed('t11', magnet=self.MAGNET, download='http://192.168.0.13:9696/8/download?apikey=secret')
        with patch.object(dp, 'realdebrid', side_effect=self.fake_rd()), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox()):
            _, codes, body = call('/api/action/download', OK, {'result': 't11'})
            _, codes2, body2 = call('/api/debrid', OK, post=False)
        blob = json.dumps(body) + json.dumps(body2)
        for secret in (self.RD_KEY, self.KEY, 'apikey=secret', self.MAGNET):
            self.assertNotIn(secret, blob)
        self.assertNotIn('downloadUrl', blob)
        self.assertNotIn('magnetUrl', blob)
        row = body2['downloads'][0]
        for held in ('debrid', 'tried', 'release', 'torbox', 'url', 'provider'):
            self.assertNotIn(held, row)
        self.assertEqual(row['state'], 'preparing')
        self.assertEqual(row['prep'], {'progress': 0, 'seeds': None, 'files': None, 'provider': 'Real-Debrid'})

    def test_an_expired_token_is_a_410(self):
        self.reset_key_cache()
        with patch.object(dp, 'realdebrid', side_effect=AssertionError('Real-Debrid must not be called')):
            _, codes, body = call('/api/action/download', OK, {'result': 'nope'})
        self.assertEqual(codes, [410])
        self.assertFalse(os.path.exists(self.state_path))

    # ------- realdebrid()'s own HTTP reading: only the base URL is local, no call is mocked -------
    def rd_server(self, status, payload):
        """Serve one Real-Debrid answer over real HTTP: (server, base URL) for a 409 or 429 style error."""

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self._reply(status, payload)

            def do_POST(self):
                self._reply(status, payload)

            def do_PUT(self):
                self._reply(status, payload)

            def _reply(self, status, payload):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(payload.encode())

            def log_message(self, *a):
                pass

        srv = HTTPServer(('127.0.0.1', 0), H)
        self.addCleanup(srv.server_close)
        t = threading.Thread(target=srv.handle_request)
        t.start()
        self.addCleanup(t.join, 5)
        return srv.server_address[1]

    def test_a_real_http_torrent_refusal_from_realdebrid_goes_to_torbox(self):
        self.reset_key_cache()
        self.seed('t20', magnet=self.MAGNET)
        port = self.rd_server(409, '{"error": "Infringing file", "error_code": 35}')
        tb = []
        with patch.object(dp, 'RD_API', 'http://127.0.0.1:%d' % port), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)):
            _, codes, body = call('/api/action/download', OK, {'result': 't20'})
        self.assertEqual(codes, [200])
        self.assertEqual(len(tb), 1)
        row = self.read_state()[0]
        self.assertEqual((row['provider'], row['tried']), ('torbox', ['realdebrid', 'torbox']))
        self.assertIn('Infringing file', row['error'])

    def test_a_real_http_rate_limit_from_realdebrid_goes_to_torbox(self):
        self.reset_key_cache()
        self.seed('t21', magnet=self.MAGNET)
        port = self.rd_server(429, '{"error": "Too many requests", "error_code": 34}')
        tb = []
        with patch.object(dp, 'RD_API', 'http://127.0.0.1:%d' % port), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)):
            _, codes, _ = call('/api/action/download', OK, {'result': 't21'})
        self.assertEqual(codes, [200])
        self.assertEqual(len(tb), 1)
        row = self.read_state()[0]
        self.assertEqual(row['provider'], 'torbox')
        self.assertIn('rate limit', row['error'])

    def test_the_realdebrid_answer_never_echoes_the_key(self):
        self.reset_key_cache()
        self.seed('t22', magnet=self.MAGNET)
        port = self.rd_server(401, '{"error": "%s", "error_code": 8}' % self.RD_KEY)
        tb = []
        with patch.object(dp, 'RD_API', 'http://127.0.0.1:%d' % port), \
             patch.object(dp, 'torbox', side_effect=self.fake_torbox(calls=tb)):
            _, codes, _ = call('/api/action/download', OK, {'result': 't22'})
        self.assertEqual(codes, [200])
        row = self.read_state()[0]
        self.assertNotIn(self.RD_KEY, json.dumps(row))
        self.assertIn('***', row['error'])


class DebridWatchTests(DebridTests):
    """Flow step 2: the tick watches a 'preparing' row at its debrid service.

    Only the services, the worker and the clock are faked; the row logic runs as written, with the
    shapes the live services answer in (checked against Real-Debrid and TorBox on 11 Nov 2026).
    """

    HASH = 'dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c'
    MAGNET = 'magnet:?xt=urn:btih:' + HASH + '&dn=Big+Buck+Bunny'

    def setUp(self):
        super().setUp()
        self.p_tb = patch.object(dp, 'TORBOX', os.path.join(self.tmp.name, 'torbox.json'))
        self.p_rd = patch.object(dp, 'REALDEBRID', os.path.join(self.tmp.name, 'realdebrid.json'))
        self.p_tb.start()
        self.p_rd.start()
        self.addCleanup(self.p_tb.stop)
        self.addCleanup(self.p_rd.stop)
        with open(os.path.join(self.tmp.name, 'torbox.json'), 'w') as f:
            json.dump({'key': 'tb-key'}, f)
        with open(os.path.join(self.tmp.name, 'realdebrid.json'), 'w') as f:
            json.dump({'key': 'rd-key'}, f)
        self.addCleanup(dp.RESULTS.clear)

    def prep_row(self, name='rd-1', provider='realdebrid', tid=1, started=None, tried=None, error=None, **over):
        """One preparing row in the state file, in the shape Get writes."""
        row = {'name': name, 'state': 'preparing', 'provider': provider, 'title': 'Big Buck Bunny', 'kind': None,
               'debrid': {'id': tid, 'hash': self.HASH}, 'release': 'Big.Buck.Bunny.2008.1080p.x264.mkv',
               'started': started if started is not None else int(time.time()), 'tried': list(tried or [provider]),
               'prep': {'progress': 0, 'seeds': None, 'files': None,
                        'provider': 'Real-Debrid' if provider == 'realdebrid' else 'TorBox'},
               'dest': None, 'bytes': 0, 'scanned': False, 'scan_error': None, 'error': error}
        row.update(over)
        self.write_state([row])
        return row

    def rd_fake(self, info=None, calls=None, fail=None):
        """Real-Debrid answers, in its own shapes: info, selectFiles, delete."""
        calls = calls if calls is not None else []

        def call(method, path, fields=None, raw=None, timeout=20):
            calls.append((method, path, fields))
            if not dp._STATE_LOCK.acquire(blocking=False):
                raise AssertionError('the state lock is held across a service call')
            dp._STATE_LOCK.release()
            if fail is not None:
                raise fail
            if path.startswith('/torrents/info'):
                return info if info is not None else {'id': 1, 'status': 'downloading', 'progress': 40,
                                                      'seeders': 12, 'files': [], 'links': []}
            return {}   # selectFiles, delete: 204 with no body
        return call

    def tb_fake(self, data=None, calls=None, fail=None):
        """TorBox answers, in its own envelope: mylist, createtorrent."""
        calls = calls if calls is not None else []

        def call(method, path, fields=(), files=(), timeout=20):
            calls.append((method, path, fields))
            if fail is not None:
                raise fail
            if path.startswith('/torrents/mylist'):
                return {'success': True, 'data': data if data is not None else
                         [{'id': 7, 'download_state': 'queued', 'progress': 0.2, 'seeds': 30,
                           'download_finished': False, 'download_present': False, 'files': []}]}
            return {'success': True, 'data': {'torrent_id': 7, 'hash': self.HASH}}
        return call

    def rd_info(self, status, progress, files, **over):
        info = {'id': 1, 'status': status, 'progress': progress, 'seeders': 12, 'files': files, 'links': [],
                'hash': self.HASH, 'filename': 'Big.Buck.Bunny.2008.1080p.x264.mkv', 'bytes': 0,
                'original_bytes': 800 * 1024 * 1024}
        info.update(over)
        return info

    def files_of(self, *entries):
        return [{'id': i, 'path': '/' + n, 'bytes': b, 'selected': 0} for i, (n, b) in enumerate(entries, 1)]

    # ------------------------------------------------------------------ progress
    def test_a_preparing_row_copies_progress_seeds_and_the_media_count(self):
        self.prep_row()
        info = self.rd_info('downloading', 40, self.files_of(('Big.Buck.Bunny.2008.1080p.x264.mkv', 800 << 20),
                                                          ('Big.Buck.Bunny.2008.1080p.x264.en.srt', 40 << 10)))
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb)), \
             patch.object(dp, '_download_rpc', side_effect=AssertionError('the worker is not for a preparing row')):
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual(row['state'], 'preparing')
        self.assertEqual((row['prep']['progress'], row['prep']['seeds'], row['prep']['files']), (0.4, 12, 1))
        self.assertEqual(row['prep']['provider'], 'Real-Debrid')
        self.assertEqual(rd[0][:2], ('GET', '/torrents/info/1'))
        self.assertFalse(rd[0][2])
        self.assertEqual(tb, [])

    def test_the_subtitle_and_the_small_trailer_are_not_media(self):
        self.prep_row()
        big = 800 << 20
        info = self.rd_info('downloading', 10, self.files_of(('Show.S01E01.1080p.x264.mkv', big),
                                                             ('Show.S01E01.SAMPLE.mkv', 120 << 20),
                                                             ('Show.S01E01.en.srt', 40 << 10),
                                                             ('trailer.mkv', 30 << 20),
                                                             ('extra.nfo', 4 << 10)))
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info)):
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual(row['prep']['files'], 1)   # only the episode itself: the sample, the subs, the
        # trailer and the .nfo all fall out of the media rule
        self.assertEqual(row['files_small'], 1)     # the 30 MB trailer, because an 800 MB file exists
        self.assertNotIn('files_cut', row)         # nothing was left out for the 60 file cap

    def test_the_file_cap_and_the_small_files_are_said_to_the_panel(self):
        self.prep_row()
        big = 800 << 20
        files = self.files_of(*[('Film.%02d.2008.1080p.x264.mkv' % i, big) for i in range(1, 62)],
                             ('trailer.mkv', 30 << 20))
        info = self.rd_info('downloading', 40, files)
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info)):
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual((row['files_small'], row['files_cut']), (1, 1))
        panel = dp._debrid_rows(dp._load_state())
        self.assertEqual((panel[0]['files_small'], panel[0]['files_cut']), (1, 1))

    def test_a_realdebrid_row_asks_for_the_media_ids_only(self):
        self.prep_row()
        big = 800 << 20
        files = self.files_of(('Show.S01E01.1080p.x264.mkv', big), ('Show.S01E01.SAMPLE.mkv', 300 << 20),
                              ('Show.S01E01.en.srt', 40 << 10))
        info = self.rd_info('waiting_files_selection', 0, files)
        rd = []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)):
            dp._debrid_tick()
        paths = [(m, p) for m, p, _ in rd]
        self.assertEqual(paths, [('GET', '/torrents/info/1'), ('POST', '/torrents/selectFiles/1')])
        self.assertEqual(rd[1][2], {'files': '1'})
        self.assertEqual(self.read_state()[0]['selected'], [1])

    def test_a_torrent_with_no_media_file_in_it_moves_off_realdebrid(self):
        self.prep_row()
        self.move_sidecar('rd-1')
        info = self.rd_info('waiting_files_selection', 0, self.files_of(('poster.jpg', 40 << 10), ('subs.srt', 4 << 10)))
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb)):
            dp._debrid_tick()
        row = self.read_state()[0]
        self.assertEqual((row['state'], row['provider']), ('preparing', 'torbox'))
        self.assertIn('no media file', row['error'])
        self.assertEqual([m + p for m, p, _ in rd], ['GET/torrents/info/1', 'DELETE/torrents/delete/1'])
        self.assertTrue(any(p.startswith('/torrents/createtorrent') for _m, p, _ in tb))

    # ------------------------------------------------------------------ fallback
    def test_a_dead_realdebrid_torrent_moves_to_torbox_once(self):
        self.prep_row()
        self.move_sidecar('rd-1')
        info = self.rd_info('dead', 0, [])
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb)):
            dp._debrid_prep_tick(time.time())
        first = self.read_state()[0]
        self.assertEqual((first['name'], first['state'], first['provider']), ('rd-1', 'preparing', 'torbox'))
        self.assertEqual(first['tried'], ['realdebrid', 'torbox'])
        self.assertEqual(first['debrid'], {'id': 7, 'hash': self.HASH})
        self.assertEqual(first['prep']['provider'], 'TorBox')
        self.assertIn('Real-Debrid: dead', first['error'])
        self.assertEqual(rd[1][:2], ('DELETE', '/torrents/delete/1'))
        self.assertEqual(tb[0][:2], ('POST', '/torrents/createtorrent'))
        self.assertEqual(tb[0][2][0], ('magnet', self.MAGNET))
        rd2, tb2 = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(calls=rd2)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb2)):
            dp._debrid_prep_tick(time.time() + dp._DEBRID_PREP_READ + 1)
        self.assertEqual(rd2, [])   # the row is at TorBox now: Real-Debrid is not asked again
        self.assertEqual(len([1 for m, p, _ in tb2 if p.startswith('/torrents/mylist')]), 1)
        self.assertEqual(len([1 for m, p, _ in tb2 if p.startswith('/torrents/createtorrent')]), 0)

    def test_a_realdebrid_row_that_made_no_progress_for_6_hours_moves(self):
        now = time.time()
        self.prep_row(started=int(now - dp._DEBRID_PREP_STALL - 120))
        self.move_sidecar('rd-1')
        info = self.rd_info('downloading', 0, [])
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb)):
            dp._debrid_prep_tick(now)
        row = self.read_state()[0]
        self.assertEqual(row['provider'], 'torbox')
        self.assertIn('no progress for 6 hours', row['error'])
        self.assertEqual(rd[1][:2], ('DELETE', '/torrents/delete/1'))

    def test_a_realdebrid_row_that_cannot_be_moved_fails_with_both_reasons(self):
        self.prep_row(error='Real-Debrid: infringing file')   # no sidecar entry: nothing to hand on
        info = self.rd_info('error', 0, [])
        rd, tb = [], []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info, calls=rd)), \
             patch.object(dp, 'torbox', side_effect=self.tb_fake(calls=tb)):
            dp._debrid_prep_tick(time.time())
        row = self.read_state()[0]
        self.assertEqual(row['state'], 'failed')
        self.assertIn('infringing file', row['error'])
        self.assertIn('the torrent for the fallback is gone', row['error'])
        self.assertEqual(tb, [])   # nothing reached TorBox

    def test_a_torbox_row_that_fails_has_nowhere_left_and_carries_both_reasons(self):
        self.prep_row(provider='torbox', tid=7, tried=['realdebrid', 'torbox'], error='Real-Debrid: infringing file')
        self.move_sidecar('rd-1')
        tb = []
        with patch.object(dp, 'torbox', side_effect=self.tb_fake(
            data=[{'id': 7, 'download_state': 'error', 'progress': 0, 'seeds': 0,
                   'download_finished': False, 'download_present': False, 'files': []}], calls=tb)):
            dp._debrid_prep_tick(time.time())
        row = self.read_state()[0]
        self.assertEqual(row['state'], 'failed')
        self.assertIn('infringing file', row['error'])
        self.assertIn('TorBox: error', row['error'])
        self.assertEqual(self.read_sidecar().get('rd-1'), None)   # the private torrent goes with the row

    def test_a_row_at_one_service_for_24_hours_fails_with_that_one_sentence(self):
        now = time.time()
        self.prep_row(started=int(now - dp._DEBRID_PREP_AGING - 60))
        self.move_sidecar('rd-1')
        info = self.rd_info('downloading', 40, [])
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=info)):
            dp._debrid_prep_tick(now)
        row = self.read_state()[0]
        self.assertEqual(row['state'], 'failed')
        self.assertEqual(row['error'], 'the debrid services did not finish this torrent in 24 hours')
        self.assertEqual(self.read_sidecar().get('rd-1'), None)

    # ------------------------------------------------------------------ private data
    def move_sidecar(self, name):
        dp._sidecar_store(name, 'magnet', self.MAGNET)

    def read_sidecar(self):
        with open(dp.DEBRID_STATE + '.prep') as f:
            return json.load(f)

    def test_the_sidecar_file_holds_the_magnet_and_is_never_world_readable(self):
        self.prep_row()
        self.move_sidecar('rd-1')
        mode = os.stat(dp.DEBRID_STATE + '.prep').st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(self.read_sidecar()['rd-1'], {'kind': 'magnet', 'magnet': self.MAGNET})
        rows = dp._debrid_rows(dp._load_state())
        blob = json.dumps(rows)
        self.assertNotIn('magnet', blob)
        self.assertNotIn('debrid', json.dumps(rows[0]).replace('"state": "preparing"', ''))

    def test_a_service_read_failure_is_kept_without_ending_the_row(self):
        self.prep_row()
        with patch.object(dp, 'realdebrid', side_effect=RuntimeError('Real-Debrid is down right now')):
            dp._debrid_prep_tick(time.time())
        row = self.read_state()[0]
        self.assertEqual(row['state'], 'preparing')
        self.assertIn('Real-Debrid is down right now', row['prep_error'])

    def test_no_row_is_read_twice_inside_10_seconds_and_only_6_rows_per_tick(self):
        now = time.time()
        rows = [dict(self.prep_row(name='rd-%d' % i, tid=i), read=now - (i * 2)) for i in range(1, 8)]
        self.write_state(rows)
        seen = []

        def info_call(method, path, fields=None, raw=None, timeout=20):
            seen.append(path)
            return self.rd_info('downloading', 1, [])
        with patch.object(dp, 'realdebrid', side_effect=info_call):
            dp._debrid_prep_tick(now)
        self.assertLessEqual(len(seen), dp._DEBRID_PREP_ROWS)
        self.assertEqual(len(seen), len(set(seen)))
        seen2 = []
        with patch.object(dp, 'realdebrid', side_effect=lambda *a, **k: seen2.append(a[1]) or {}):
            dp._debrid_prep_tick(now + 1)
        self.assertEqual(seen2, [])   # every row was read inside the last 10 s

    def test_a_row_that_is_gone_hands_over_nothing(self):
        """A row that left the list between the tick's read and its refresh hands over 0 files, and a
        vanished row is never read at a service."""
        with patch.object(dp, 'realdebrid', side_effect=AssertionError('no row, no read')):
            self.assertEqual(dp._prep_refresh('rd-not-here', time.time(), 2), 0)

    def test_the_download_tick_never_asks_the_worker_about_a_preparing_row(self):
        self.prep_row()
        self.write_state([{'name': 'rd-1', 'state': 'preparing', 'provider': 'realdebrid', 'title': 'T',
                          'kind': None, 'debrid': {'id': 1, 'hash': self.HASH}, 'dest': None, 'bytes': 0,
                          'started': int(time.time()), 'scanned': False, 'tried': ['realdebrid'],
                          'release': 'r', 'prep': {'progress': 0, 'seeds': None, 'files': None,
                                                  'provider': 'Real-Debrid'}, 'error': None},
                         {'name': 'Dune.2021.mkv', 'state': 'finished', 'dest': '/mnt/unraid/Movies',
                          'bytes': 1 << 30, 'scanned': False, 'started': int(time.time())}])
        jobs = []

        def rpc(payload, timeout=45):
            jobs.append(payload['jobs'])
            return {'ok': True, 'jobs': [{'dest': '/mnt/unraid/Movies', 'name': 'Dune.2021.mkv',
                                         'state': 'finished', 'bytes': 1 << 30, 'total_bytes': 1 << 30}]}
        with patch.object(dp, 'realdebrid', side_effect=self.rd_fake(info=self.rd_info('downloading', 5, []))), \
             patch.object(dp, '_download_rpc', side_effect=rpc), \
             patch.object(dp, '_jellyfin_scan', return_value=True):
            dp._debrid_tick()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0], [{'dest': '/mnt/unraid/Movies', 'name': 'Dune.2021.mkv'}])


class DebridHandoverTests(DebridTests):
    """Flow step 3: a finished torrent hands its files to the download worker, two per tick.

    The services, the worker and the sleep between their calls are faked; the folder rules, the naming
    rules and the row shape are the real ones.
    """

    RD_KEY = 'rdkeyABCDEF0123456789'
    TB_KEY = 'tbkeyZZZZ0123456789abcdef'
    HASH = 'dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c'
    MOVIE = 'Big.Buck.Bunny.2008.1080p.x264.mkv'
    PACK = 'Show.County.Building.S02.1080p.x264'
    SIZE = 800 * 1024 * 1024

    def setUp(self):
        super().setUp()
        for const, value in (('TORBOX', os.path.join(self.tmp.name, 'torbox.json')),
                             ('REALDEBRID', os.path.join(self.tmp.name, 'realdebrid.json'))):
            p = patch.object(dp, const, value)
            p.start()
            self.addCleanup(p.stop)
        with open(os.path.join(self.tmp.name, 'torbox.json'), 'w') as f:
            json.dump({'key': self.TB_KEY}, f)
        with open(os.path.join(self.tmp.name, 'realdebrid.json'), 'w') as f:
            json.dump({'key': self.RD_KEY}, f)
        self.waits = []
        p = patch.object(dp, '_sleep', side_effect=lambda s: self.waits.append(s))
        p.start()
        self.addCleanup(p.stop)
        self.started = []

        def rpc(payload, timeout=45):
            self.started.append(payload)
            return {'ok': True, 'unit': 'dl-' + payload['name'], 'state': 'downloading'}
        p = patch.object(dp, '_download_rpc', side_effect=rpc)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(dp.RESULTS.clear)
        self.addCleanup(setattr, dp, '_RD_CALL_AT', 0.0)
        self.addCleanup(setattr, dp, '_TB_CALL_AT', 0.0)

    def prep_row(self, provider='realdebrid', tid=1, release=None, name='rd-1', handed=None, **over):
        row = {'name': name, 'state': 'preparing', 'provider': provider, 'title': 'Big Buck Bunny', 'kind': None,
               'debrid': {'id': tid, 'hash': self.HASH},
               'release': release or self.MOVIE, 'started': int(time.time()),
               'tried': ['realdebrid'] if provider == 'realdebrid' else ['realdebrid', 'torbox'],
               'prep': {'progress': 1, 'seeds': None, 'files': None,
                        'provider': 'Real-Debrid' if provider == 'realdebrid' else 'TorBox'},
               'dest': None, 'bytes': 0, 'scanned': False, 'scan_error': None, 'error': None}
        if handed:
            row['handed'] = list(handed)
        row.update(over)
        self.write_state([row])
        return row

    def rd_files(self, names, selected=True):
        out = []
        for i, n in enumerate(names, 1):
            entry = {'id': i, 'path': '/' + n, 'bytes': self.SIZE, 'selected': 1 if selected else 0}
            out.append(entry)
        return out

    def rd_service(self, names, links=None, answers=None, calls=None, archive=None):
        """One fake Real-Debrid that answers info, unrestrict and delete in its own shapes."""
        calls = calls if calls is not None else []
        links = links if links is not None else ['https://rd-lan-invalid.dl/l/%d' % i for i in range(1, len(names) + 1)]
        answers = answers if answers is not None else [
            {'download': 'https://rd-dl-invalid.dl/f/%d/%s' % (i, n), 'filename': n, 'filesize': self.SIZE}
            for i, n in enumerate(names, 1)]

        def call(method, path, fields=None, raw=None, timeout=20):
            calls.append({'method': method, 'path': path, 'fields': fields})
            if path.startswith('/torrents/info'):
                return {'id': 1, 'status': 'downloaded', 'progress': 100, 'hash': self.HASH,
                        'files': self.rd_files(names), 'links': links,
                        'filename': names[0], 'bytes': self.SIZE * len(names), 'original_bytes': self.SIZE * len(names)}
            if path == '/unrestrict/link':
                if archive is not None:
                    a = archive.pop(0) if isinstance(archive, list) else archive
                    return a
                return answers.pop(0)
            return {}
        return call

    def tb_service(self, names, calls=None, link=None):
        """One fake TorBox: mylist says the files are there, requestdl answers one https link."""
        calls = calls if calls is not None else []
        link = link or 'https://tb-dl-invalid.dl/dld/uuid?token=abc'

        def call(method, path, fields=(), files=(), timeout=20, json_body=None):
            calls.append({'method': method, 'path': path, 'fields': fields, 'json_body': json_body})
            if path.startswith('/torrents/mylist'):
                return {'success': True, 'data': [{'id': 7, 'download_state': 'cached', 'progress': 1,
                                                  'seeds': 4, 'download_finished': True, 'download_present': True,
                                                  'files': [{'id': i, 'name': n, 'size': self.SIZE, 'infected': False}
                                                          for i, n in enumerate(names, 1)]}]}
            if path.startswith('/torrents/requestdl'):
                return {'success': True, 'data': link}
            return {'success': True, 'data': {'torrent_id': 7, 'hash': self.HASH}}
        return call

    # ------------------------------------------------------------------ one file
    def test_a_second_watching_row_is_still_read_when_one_row_hands_two_files(self):
        """The hand-over budget is not the read budget: a row that already spent both files this tick
        must not freeze the other watching rows' progress."""
        now = time.time()
        names = ['Film.A.2008.1080p.x264.mkv', 'Film.B.2008.1080p.x264.mkv']
        one = self.prep_row(name='rd-1', tid=1, release=names[0], read=now - 60)
        two = self.prep_row(name='rd-2', tid=2, release=names[1], read=now - 60)
        self.write_state([one, two])
        seen = []

        def call(method, path, fields=None, raw=None, timeout=20):
            seen.append(method + ' ' + path)
            if path == '/torrents/info/1':
                return {'id': 1, 'status': 'downloaded', 'progress': 100, 'hash': self.HASH,
                        'files': self.rd_files(names),
                        'links': ['https://rd-lan-invalid.dl/l/1', 'https://rd-lan-invalid.dl/l/2'],
                        'filename': names[0], 'bytes': self.SIZE * 2, 'original_bytes': self.SIZE * 2}
            if path == '/torrents/info/2':
                return {'id': 2, 'status': 'downloading', 'progress': 22, 'seeders': 5, 'hash': 'b' * 40,
                        'files': [{'id': 1, 'path': '/' + names[1], 'bytes': self.SIZE, 'selected': 1}],
                        'links': [], 'filename': names[1], 'bytes': self.SIZE, 'original_bytes': self.SIZE}
            if path == '/unrestrict/link':
                n = len([c for c in seen if c.endswith('/unrestrict/link')])
                return {'download': 'https://rd-dl-invalid.dl/f/%d/x.mkv' % n, 'filename': names[n - 1],
                        'filesize': self.SIZE}
            return {}
        with patch.object(dp, 'realdebrid', side_effect=call):
            dp._debrid_prep_tick(now)
        rows = {r['name']: r for r in self.read_state()}
        self.assertIn('GET /torrents/info/2', seen)      # read, even with the hand-over budget spent
        self.assertEqual((rows['rd-2']['prep']['progress'], rows['rd-2']['prep']['seeds']), (0.22, 5))
        self.assertEqual(len(self.started), 2)          # and still at most two files this tick
        self.assertEqual(sorted(c['name'] for c in self.started), sorted(names))
        self.assertNotIn('rd-1', rows)                    # both files went, so rd-1's watching row retired
        self.assertEqual(rows['rd-2']['state'], 'preparing')

    def test_a_ready_torrent_becomes_one_download_row_under_movies(self):
        self.prep_row(release=self.MOVIE)
        rd = []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_service([self.MOVIE], calls=rd)):
            handed = dp._debrid_prep_tick(time.time())
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0]['cmd'], 'start')
        self.assertEqual(self.started[0]['url'], 'https://rd-dl-invalid.dl/f/1/%s' % self.MOVIE)
        self.assertEqual(self.started[0]['dest'], '/mnt/unraid/Movies')
        self.assertEqual(self.started[0]['name'], self.MOVIE)
        rows = dp._debrid_rows(dp._load_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['state'], rows[0]['dest'], rows[0]['kind']), ('downloading', '/mnt/unraid/Movies', 'movie'))
        self.assertEqual(rows[0]['title'], 'Big Buck Bunny')
        self.assertFalse(any(d.get('state') == 'preparing' for d in dp._load_state()['downloads']))
        self.assertEqual(dp._sidecar_load(), {})   # the private torrent left with the row

    def test_the_link_is_kept_on_the_server_and_never_shown(self):
        self.prep_row(release=self.MOVIE)
        with patch.object(dp, 'realdebrid', side_effect=self.rd_service([self.MOVIE])):
            dp._debrid_prep_tick(time.time())
        row = dp._load_state()['downloads'][0]
        # the hoster link, not the resolved one: a retry re-asks it, and a resolved link goes dead
        self.assertEqual(row['debrid'], {'id': 1, 'file': 1, 'link': 'https://rd-lan-invalid.dl/l/1'})
        self.assertIsNone(row['url'])   # the worker got a url; the row keeps only the service call
        self.assertEqual(row['provider'], 'realdebrid')
        _, codes, body = call('/api/debrid', OK, post=False)
        blob = json.dumps(body)
        self.assertNotIn('rd-dl-invalid', blob)
        self.assertNotIn('rd-lan-invalid', blob)
        self.assertNotIn(self.RD_KEY, blob)
        self.assertNotIn('debrid', json.dumps(body['downloads'][0]))
        self.assertNotIn('release', json.dumps(body['downloads'][0]))
        self.assertNotIn('tried', json.dumps(body['downloads'][0]))

    # ------------------------------------------------------------------ a pack
    def test_a_pack_goes_two_files_at_a_time_under_one_show_folder(self):
        names = ['S02E%02d.mkv' % i for i in range(1, 11)]
        self.prep_row(release=self.PACK, name='rd-9')
        for tick in range(3):
            with patch.object(dp, 'realdebrid', side_effect=self.rd_service(names)):
                dp._debrid_prep_tick(time.time() + (tick * 30))
        self.assertEqual(len(self.started), 6)          # two files per tick, not ten at once
        rows = dp._load_state()['downloads']
        prep = [d for d in rows if d.get('state') == 'preparing']
        self.assertEqual(len(prep), 1)
        self.assertEqual(prep[0]['handed'], [1, 2, 3, 4, 5, 6])
        self.assertEqual(prep[0]['prep']['files'], 4)   # files left to hand over
        made = [d for d in rows if d.get('state') == 'downloading']
        self.assertEqual(len(made), 6)
        self.assertEqual({d['dest'] for d in made}, {'/mnt/unraid/Shows/Show County Building'})
        self.assertEqual([(d['season'], d['episode']) for d in made], [(2, i) for i in range(1, 7)])
        self.assertEqual({d['kind'] for d in made}, {'show'})
        self.assertEqual(made[0]['title'], 'Show County Building')   # the file name S02E01 names no show
        self.assertEqual(made[0]['kind_source'], 'torrent')

    def test_a_pack_finishes_and_its_watching_row_goes_at_the_last_file(self):
        names = ['S02E%02d.mkv' % i for i in range(1, 5)]
        self.prep_row(release=self.PACK, name='rd-8')
        for tick in range(2):
            with patch.object(dp, 'realdebrid', side_effect=self.rd_service(names)):
                dp._debrid_prep_tick(time.time() + (tick * 30))
        self.assertEqual(len(self.started), 4)
        self.assertEqual([d for d in dp._load_state()['downloads'] if d.get('state') == 'preparing'], [])
        self.assertEqual(dp._sidecar_load(), {})

    # ------------------------------------------------------------------ the fallback case
    def test_a_realdebrid_answer_packed_into_an_archive_goes_to_torbox_instead(self):
        names = ['Movie.1995.Part1.mkv', 'Movie.1995.Part2.mkv']
        self.prep_row(release='Movie.1995.1080p.x264', name='rd-7')
        dp._sidecar_store('rd-7', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        rd, tb = [], []
        rd_service = self.rd_service(names, calls=rd, archive={'download': 'https://rd-dl-invalid.dl/f/1/part.rar',
                                                              'filename': 'Movie.1995.rar', 'filesize': 2 << 30})
        with patch.object(dp, 'realdebrid', side_effect=rd_service), \
             patch.object(dp, 'torbox', side_effect=self.tb_service(names, calls=tb)):
            dp._debrid_prep_tick(time.time())
        self.assertEqual(self.started, [])   # nothing was handed over at Real-Debrid
        row = dp._load_state()['downloads'][0]
        self.assertEqual((row['state'], row['provider']), ('preparing', 'torbox'))
        self.assertIn('packed into an archive', row['error'])
        self.assertEqual(row['tried'], ['realdebrid', 'torbox'])
        self.assertEqual(rd[-1]['method'] + rd[-1]['path'], 'DELETE/torrents/delete/1')
        self.assertTrue(any(c['path'].startswith('/torrents/createtorrent') for c in tb))

    def test_a_packed_answer_after_a_started_file_keeps_that_file_and_skips_only_the_packed_one(self):
        """A packed answer mid-tick: the file already started on the worker keeps its row, and the
        fallback hands over only the packed file, not the one already downloading."""
        names = ['Movie.1995.Part1.mkv', 'Movie.1995.Part2.mkv']
        self.prep_row(release='Movie.1995.1080p.x264', name='rd-7')
        dp._sidecar_store('rd-7', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        rd, tb = [], []
        # the first answer is normal (Part1 starts), the second is packed (Part2 goes to the fallback)
        rd_service = self.rd_service(names, calls=rd, archive=[
            {'download': 'https://rd-dl-invalid.dl/f/1/%s' % names[0], 'filename': names[0], 'filesize': self.SIZE},
            {'download': 'https://rd-dl-invalid.dl/f/2/movie.rar', 'filename': 'Movie.1995.rar', 'filesize': 2 << 30}])

        def tb_call(method, path, fields=(), files=(), timeout=20, json_body=None):
            tb.append({'method': method, 'path': path})
            if path.startswith('/torrents/mylist'):
                # ids 5 and 6 do not collide with Real-Debrid's 1 and 2: the skip must be by name
                return {'success': True, 'data': [{'id': 7, 'download_state': 'cached', 'progress': 1,
                                                  'seeds': 4, 'download_finished': True, 'download_present': True,
                                                  'files': [{'id': 5, 'name': names[0], 'size': self.SIZE, 'infected': False},
                                                            {'id': 6, 'name': names[1], 'size': self.SIZE, 'infected': False}]}]}
            if path.startswith('/torrents/requestdl'):
                return {'success': True, 'data': 'https://tb-dl-invalid.dl/dld/uuid?token=abc'}
            return {'success': True, 'data': {'torrent_id': 7, 'hash': self.HASH}}
        with patch.object(dp, 'realdebrid', side_effect=rd_service), \
             patch.object(dp, 'torbox', side_effect=tb_call):
            dp._debrid_prep_tick(time.time())
        # Part1 is on the worker and has a row; the packed file has neither
        self.assertEqual([c['name'] for c in self.started], [names[0]])
        rows = {d['name']: d for d in dp._load_state()['downloads']}
        self.assertEqual((rows[names[0]]['state'], rows[names[0]]['provider']), ('downloading', 'realdebrid'))
        self.assertEqual(rows[names[0]]['debrid'], {'id': 1, 'file': 1, 'link': 'https://rd-lan-invalid.dl/l/1'})
        watch = rows['rd-7']
        self.assertEqual((watch['state'], watch['provider']), ('preparing', 'torbox'))
        self.assertIn('packed into an archive', watch['error'])
        self.assertEqual(watch['handed_names'], [names[0]])
        self.assertTrue(any(c['method'] + c['path'] == 'DELETE/torrents/delete/1' for c in rd))
        self.assertTrue(any(c['path'].startswith('/torrents/createtorrent') for c in tb))
        # the next tick: the fallback hands over only the packed file
        with patch.object(dp, 'realdebrid', side_effect=rd_service), \
             patch.object(dp, 'torbox', side_effect=tb_call):
            dp._debrid_prep_tick(time.time() + 30)
        self.assertEqual([c['name'] for c in self.started], [names[0], names[1]])
        rows = dp._load_state()['downloads']
        self.assertEqual([d for d in rows if d.get('state') == 'preparing'], [])
        part2 = next(d for d in rows if d['name'] == names[1])
        self.assertEqual(part2['provider'], 'torbox')
        self.assertEqual(part2['debrid'], {'id': 7, 'file': 6, 'link': None})
        self.assertEqual(dp._sidecar_load(), {})

    # ------------------------------------------------------------------ the other service
    def test_torbox_is_asked_its_way_and_its_link_never_asks_for_a_redirect(self):
        self.prep_row(provider='torbox', tid=7, name='torbox-7', release=self.MOVIE)
        dp._sidecar_store('torbox-7', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        tb = []
        with patch.object(dp, 'torbox', side_effect=self.tb_service([self.MOVIE], calls=tb)), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('a TorBox row stays at TorBox')):
            dp._debrid_prep_tick(time.time())
        asked = [c['path'] for c in tb]
        self.assertTrue(any(p.startswith('/torrents/requestdl') for p in asked))
        self.assertEqual([p for p in asked if 'redirect' in p], [])
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0]['url'], 'https://tb-dl-invalid.dl/dld/uuid?token=abc')
        self.assertEqual(self.started[0]['dest'], '/mnt/unraid/Movies')

    def test_a_torbox_link_and_key_never_reach_the_panel(self):
        self.prep_row(provider='torbox', tid=7, name='torbox-7', release=self.MOVIE)
        dp._sidecar_store('torbox-7', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        with patch.object(dp, 'torbox', side_effect=self.tb_service([self.MOVIE],
                                                                  link='https://tb-dl-invalid.dl/dld/u?token=' + self.TB_KEY)):
            dp._debrid_prep_tick(time.time())
        _, codes, body = call('/api/debrid', OK, post=False)
        blob = json.dumps(body)
        self.assertNotIn(self.TB_KEY, blob)
        self.assertNotIn('tb-dl-invalid', blob)
        row = dp._load_state()['downloads'][0]
        self.assertEqual((row['debrid']['id'], row['debrid']['file']), (7, 1))
        self.assertIsNone(row['debrid']['link'])   # TorBox re-asks by ids, so no link of its own is kept

    # ------------------------------------------------------------------ spacing
    def test_realdebrid_is_not_asked_twice_within_a_second(self):
        names = ['S02E%02d.mkv' % i for i in range(1, 6)]
        self.prep_row(release=self.PACK, name='rd-5')
        with patch.object(dp, 'realdebrid', side_effect=self.rd_service(names)):
            dp._debrid_prep_tick(time.time())
            dp._debrid_prep_tick(time.time() + dp._DEBRID_PREP_READ)
        self.assertEqual(len(self.started), 4)   # two per tick
        # info + unrestrict per file, and every unrestrict pair sits at least a second apart
        self.assertTrue(all(w >= dp._RD_CALL_SPACING for w in self.waits))
        self.assertLessEqual(len(self.waits), 4)

    def test_torbox_download_links_are_at_least_3_seconds_apart(self):
        names = ['S02E%02d.mkv' % i for i in range(1, 6)]
        self.prep_row(provider='torbox', tid=7, name='torbox-7', release=self.PACK)
        dp._sidecar_store('torbox-7', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        with patch.object(dp, 'torbox', side_effect=self.tb_service(names)):
            dp._debrid_prep_tick(time.time())
            dp._debrid_prep_tick(time.time() + dp._DEBRID_PREP_READ)
        self.assertEqual(len(self.started), 4)
        self.assertEqual(len(self.waits), 2)   # the second file of each tick waited
        self.assertTrue(all(w >= dp._TB_CALL_SPACING for w in self.waits))


class DebridFinishTests(DebridHandoverTests):
    """Flow steps 4, 5 and 6: retry re-asks the row's provider, Cancel frees the torrent, the panel
    learns the flow is there. Same fakes as step 3: the services, the worker and the sleeps."""

    # ------------------------------------------------------------------ Cancel
    def test_cancel_frees_the_torrent_at_realdebrid_and_leaves_no_row_behind(self):
        self.prep_row(name='rd-1')
        dp._sidecar_store('rd-1', 'magnet', 'magnet:?xt=urn:btih:' + self.HASH)
        rd = []
        with patch.object(dp, 'realdebrid', side_effect=self.rd_service([self.MOVIE], calls=rd)), \
             patch.object(dp, 'torbox', side_effect=AssertionError('a Real-Debrid row is freed at Real-Debrid')):
            _, codes, body = call('/api/debrid', OK, {'cancel': 'rd-1'})
        self.assertEqual(codes, [200], body)
        self.assertTrue(body['ok'])
        self.assertEqual([c['method'] + c['path'] for c in rd], ['DELETE/torrents/delete/1'])
        self.assertEqual(dp._load_state()['downloads'], [])
        self.assertEqual(dp._sidecar_load(), {})
        self.assertNotIn('cancel', json.dumps(rd))   # the key file, the magnet and the release stay out

    def test_cancel_at_torbox_uses_its_controltorrent_call(self):
        self.prep_row(provider='torbox', tid=7, name='torbox-7')
        tb = []
        with patch.object(dp, 'torbox', side_effect=self.tb_service([self.MOVIE], calls=tb)), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('not this row')):
            _, codes, body = call('/api/debrid', OK, {'cancel': 'torbox-7'})
        asked = [(c['method'], c['path']) for c in tb]
        self.assertEqual(asked, [('POST', '/torrents/controltorrent')])
        self.assertEqual(tb[0]['json_body'], {'torrent_id': 7, 'operation': 'delete'})
        self.assertEqual(dp._load_state()['downloads'], [])

    def test_a_row_that_already_handed_its_files_over_is_not_cancellable(self):
        self.write_state([dict(name=self.MOVIE, state='downloading', kind='movie', title='Big Buck Bunny',
                               dest='/mnt/unraid/Movies', debrid={'id': 1, 'file': 1, 'link': 'https://x.invalid/a'},
                               bytes=1, started=int(time.time()))])
        _, codes, body = call('/api/debrid', OK, {'cancel': self.MOVIE})
        self.assertEqual(codes, [400])
        self.assertIn('Cancel is for a row still waiting at its service', body['error'])

    def test_clear_keeps_a_row_that_is_still_at_its_service(self):
        self.prep_row(name='rd-2')
        self.write_state([d for d in dp._load_state()['downloads']] +
                         [dict(name='Old.Broken.mkv', state='failed', kind='movie', title='Old Broken',
                               dest='/mnt/unraid/Movies', bytes=0, started=int(time.time()),
                               error='link dead', url=None, retried=False, torbox=None)])
        _, codes, body = call('/api/debrid', OK, {'clear': True})
        self.assertEqual(body['cleared'], 1)
        self.assertEqual([d['name'] for d in dp._load_state()['downloads']], ['rd-2'])

    def test_a_service_refusal_still_frees_the_row_and_says_so(self):
        self.prep_row(name='rd-3')

        def refusing(method, path, fields=None, raw=None, timeout=20):
            raise RuntimeError('torrent is being downloaded')
        with patch.object(dp, 'realdebrid', side_effect=refusing):
            _, codes, body = call('/api/debrid', OK, {'cancel': 'rd-3'})
        self.assertEqual(codes, [200])
        self.assertIn('Real-Debrid did not free its torrent', body['message'])
        self.assertEqual(dp._load_state()['downloads'], [])

    # ------------------------------------------------------------------ Retry
    def test_retry_asks_realdebrid_again_for_a_link_and_starts_it(self):
        self.write_state([dict(name=self.MOVIE, state='failed', kind='movie', title='Big Buck Bunny',
                               dest='/mnt/unraid/Movies', bytes=0, started=int(time.time()), error='link died',
                               url=None, retried=False, provider='realdebrid',
                               debrid={'id': 1, 'file': 1, 'link': 'https://rd-lan-invalid.dl/l/1'},
                               state_since=0, scanned=True, scan_error=None, grounded=None,
                               total_bytes=self.SIZE, speed=0, eta='', prep=None, tried=['realdebrid'])])
        seen = []

        def unrestrict(method, path, fields=None, raw=None, timeout=20):
            seen.append(fields)
            return {'download': 'https://rd-dl-invalid.dl/f/9/%s' % self.MOVIE, 'filename': self.MOVIE,
                    'filesize': self.SIZE}
        with patch.object(dp, 'realdebrid', side_effect=unrestrict):
            codes, body = call('/api/debrid', OK, {'retry': self.MOVIE})[1], call('/api/debrid', OK, {'retry': self.MOVIE})[2]
        self.assertEqual(codes, [200])
        self.assertEqual(seen, [{'link': 'https://rd-lan-invalid.dl/l/1'}])   # the stored hoster link, re-asked
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0]['url'], 'https://rd-dl-invalid.dl/f/9/%s' % self.MOVIE)
        self.assertEqual(self.started[0]['dest'], '/mnt/unraid/Movies')
        row = dp._load_state()['downloads'][0]
        self.assertEqual(row['state'], 'downloading')
        self.assertIsNone(row['url'])   # still no dead link kept: a second retry re-asks too
        self.assertEqual(row['debrid']['link'], 'https://rd-lan-invalid.dl/l/1')

    def test_a_handed_over_row_offers_retry_in_the_panel(self):
        self.write_state([dict(name=self.MOVIE, state='failed', kind='movie', title='Big Buck Bunny',
                               dest='/mnt/unraid/Movies', bytes=0, started=int(time.time()), error='link died',
                               url=None, retried=False, provider='realdebrid',
                               debrid={'id': 1, 'file': 1, 'link': 'https://rd-lan-invalid.dl/l/1'})])
        _, _, body = call('/api/debrid', OK, post=False)
        self.assertTrue(body['downloads'][0]['can_retry'])
        self.assertNotIn('debrid', json.dumps(body['downloads'][0]))
        self.assertNotIn('rd-lan-invalid', json.dumps(body['downloads'][0]))

    def test_torbox_retry_asks_its_requestdl_again_by_id(self):
        self.write_state([dict(name=self.MOVIE, state='stopped', kind='movie', title='Big Buck Bunny',
                               dest='/mnt/unraid/Movies', bytes=0, started=int(time.time()), error='stopped by hand',
                               url=None, retried=False, provider='torbox',
                               debrid={'id': 7, 'file': 3, 'link': None})])
        tb = []

        def answer(method, path, fields=(), files=(), timeout=20):
            tb.append({'method': method, 'path': path, 'fields': fields})
            return {'success': True, 'data': 'https://tb-dl-invalid.dl/dld/second?token=abc'}
        with patch.object(dp, 'torbox', side_effect=answer), \
             patch.object(dp, 'realdebrid', side_effect=AssertionError('a TorBox row re-asks TorBox')):
            dp.retry_debrid(self.MOVIE)
        self.assertEqual(len(tb), 1)
        self.assertIn('/torrents/requestdl', tb[0]['path'])
        self.assertIn('torrent_id=7', tb[0]['path'])
        self.assertIn('file_id=3', tb[0]['path'])
        self.assertNotIn('redirect', tb[0]['path'])
        self.assertEqual(self.started[0]['url'], 'https://tb-dl-invalid.dl/dld/second?token=abc')

    def test_a_row_with_neither_a_link_nor_a_service_call_is_refused(self):
        self.write_state([dict(name='Old.Thing.mkv', state='failed', kind='movie', title='Old Thing',
                               dest='/mnt/unraid/Movies', bytes=0, started=int(time.time()), error='link died',
                               url=None, retried=False, torbox=None)])
        _, codes, body = call('/api/debrid', OK, {'retry': 'Old.Thing.mkv'})
        self.assertEqual(codes, [400])
        self.assertIn('paste a fresh one', body['error'])


class DebridCancelRaceTests(unittest.TestCase):
    """Owns only the state file: the race is inside _prep_update, no service or worker is involved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, 'debrid.json')
        p = patch.object(dp, 'DEBRID_STATE', self.state_path)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_state(self, rows):
        with open(self.state_path, 'w') as f:
            json.dump({'downloads': rows}, f)

    def read_state(self):
        with open(self.state_path) as f:
            return json.load(f)['downloads']

    # Review item 4 (11 Nov 2026): the tick hands files to the worker, then writes the rows back. If
    # Cancel removed the preparing row meanwhile, the started rows must still land, or the download runs
    # with no row: no progress line, no Stop, and the service hands the same file out again.

    STARTED = [{'dest': '/mnt/unraid/Movies', 'name': 'Dune.2021.2160p.mkv', 'unit': 'dl-Dune.2021.2160p.mkv',
                'state': 'downloading', 'kind': 'movie', 'title': 'Dune', 'bytes': 0, 'started': 1}]

    def test_a_cancelled_parent_still_gets_its_started_rows(self):
        self.write_state([])          # Cancel took the preparing row away mid-tick
        self.assertFalse(dp._prep_update('rd-Dune.2021.2160p', lambda r: r.update(seen=1), add_rows=self.STARTED))
        rows = self.read_state()
        self.assertEqual([d['name'] for d in rows], ['Dune.2021.2160p.mkv'])
        self.assertEqual(rows[0]['unit'], 'dl-Dune.2021.2160p.mkv')
        self.assertEqual(rows[0]['state'], 'downloading')

    def test_the_same_rows_are_added_once(self):
        self.write_state([])
        dp._prep_update('rd-Dune.2021.2160p', lambda r: None, add_rows=self.STARTED)
        dp._prep_update('rd-Dune.2021.2160p', lambda r: None, add_rows=self.STARTED)
        self.assertEqual(len(self.read_state()), 1)

    def test_a_parent_that_ended_during_the_handover_keeps_its_files_too(self):
        # A row closed by the 24 hour clock is no longer 'preparing'; the worker still got its file.
        self.write_state([{'name': 'rd-Dune.2021.2160p', 'dest': '/mnt/unraid/Movies', 'state': 'failed',
                       'error': 'did not finish this torrent in 24 hours'}])
        self.assertFalse(dp._prep_update('rd-Dune.2021.2160p', lambda r: r.update(seen=1), add_rows=self.STARTED))
        rows = self.read_state()
        self.assertEqual([d['name'] for d in rows], ['rd-Dune.2021.2160p', 'Dune.2021.2160p.mkv'])
        self.assertEqual(rows[0]['state'], 'failed')     # the parent keeps the reason, not the mutation

    def test_a_cancel_with_nothing_started_still_leaves_nothing(self):
        self.write_state([])
        self.assertFalse(dp._prep_update('rd-Dune.2021.2160p', lambda r: r.update(seen=1)))
        self.assertEqual(self.read_state(), [])


class DebridSpaceTests(DebridTests):
    """Task 1 in docs/SPEC-downloads-augmentations.md: the worker states free space on the share, and a
    start that would leave less than RESERVE_BYTES free is refused before anything is asked of a
    service. An unknown size or a missing reading never blocks a download."""

    GB = 1024 ** 3

    def worker(self, free=None, fail=False):
        """A fake n100 worker: 'space' answers free_bytes, 'start' answers a started unit."""
        def call(payload, timeout=None):
            if payload.get('cmd') == 'space':
                if fail:
                    raise RuntimeError('the download worker on the n100 did not answer in time')
                return {'ok': True, 'free_bytes': free, 'total_bytes': free}
            return {'ok': True, 'unit': 'dl-Memento.2000.BluRay.1080p.mkv', 'state': 'downloading',
                    'message': 'started'}
        return call

    def add_with(self, free=None, size=None, fail=False, state=None):
        """One pasted link through the space check; returns (answer-or-None, error, worker calls)."""
        if state is not None:
            self.write_state(state)
        calls = []
        with patch.object(dp, '_download_rpc', side_effect=lambda p, timeout=None: (calls.append(p),
                                                                                    self.worker(free, fail)(p)) [1]), \
             patch.object(dp, '_content_length', return_value=size):
            try:
                return dp.add_debrid('https://nyk.example.com/d/abc/Memento.2000.BluRay.1080p.mkv'), None, calls
            except dp.NoSpace as exc:
                return None, str(exc), calls

    def test_it_starts_when_the_share_keeps_its_reserve(self):
        out, err, calls = self.add_with(free=900 * self.GB, size=80 * self.GB)
        self.assertTrue(out['ok'], err)
        self.assertIn('start', [c['cmd'] for c in calls])
        self.assertEqual((out['size_bytes'], out['free_bytes'], out['free_after_bytes'], out['fits']),
                         (80 * self.GB, 900 * self.GB, 820 * self.GB, True))

    def test_it_refuses_a_grab_that_would_eat_the_array(self):
        out, err, calls = self.add_with(free=120 * self.GB, size=80 * self.GB)
        self.assertIsNone(out)
        self.assertIn('will not fit', err)
        self.assertIn('50 GB', err)                    # the reserve, in words
        self.assertNotIn('start', [c['cmd'] for c in calls])   # nothing reached the worker
        self.assertFalse(os.path.exists(self.state_path))     # and no row was remembered

    def test_a_size_it_cannot_know_never_blocks_a_download(self):
        # 30 GB free is under the reserve, but a link that states no size is not refused: the preview
        # says the size is not known instead.
        out, err, calls = self.add_with(free=30 * self.GB, size=None)
        self.assertTrue(out['ok'], err)
        self.assertIn('start', [c['cmd'] for c in calls])
        self.assertEqual((out['size_bytes'], out['fits']), (None, True))

    def test_a_worker_that_says_nothing_does_not_block_a_download(self):
        # The reading failed, so the check fails open: free_bytes stays None and the start still ran.
        out, err, calls = self.add_with(fail=True, size=80 * self.GB)
        self.assertTrue(out['ok'], err)
        self.assertIn('start', [c['cmd'] for c in calls])
        self.assertEqual((out['free_bytes'], out['free_after_bytes'], out['fits']), (None, None, None))

    def test_downloads_now_running_count_against_free_space(self):
        # 50 GB of a 500 GB file are still to be written by a download already running, so a 60 GB
        # grab would leave 20 GB: refused, even though 130 GB reads as free.
        running = {'name': 'Big.2024.2160p.mkv', 'dest': dp.MOVIES_DIR, 'state': 'downloading',
                   'total_bytes': 500 * self.GB, 'bytes': 450 * self.GB}
        out, err, calls = self.add_with(free=130 * self.GB, size=60 * self.GB, state=[running])
        self.assertIsNone(out)
        self.assertNotIn('start', [c['cmd'] for c in calls])
        self.assertIn('will not fit', err)

    def test_a_search_get_is_refused_before_the_torrent_reaches_a_service(self):
        self.write_state([])
        asked = []
        # The token carries the indexer's own size; the guard runs before either service is asked.
        token = 'tk'
        with patch.object(dp, 'RESULTS', {token: {'at': time.time(), 'title': 'Dune 2021', 'infoHash': 'a' * 40,
                                                 'protocol': 'torrent', 'size': 80 * self.GB}}), \
             patch.object(dp, '_indexer_torrent', return_value=('file', b't')),\
             patch.object(dp, '_rd_key', return_value='key'), \
             patch.object(dp, '_rd_add_torrent', side_effect=lambda k, p: asked.append('rd') or ('1', 'h')),\
             patch.object(dp, '_download_rpc', side_effect=self.worker(free=60 * self.GB)):
            with self.assertRaises(dp.NoSpace):
                dp.grab_debrid(token)
        self.assertEqual(asked, [])   # Real-Debrid was never asked for the torrent

    def test_indexer_sizes_are_read_in_both_forms(self):
        self.assertEqual(dp._indexer_bytes(1500000000), 1500000000)
        self.assertEqual(dp._indexer_bytes('1.5 GB'), int(1.5 * self.GB))
        self.assertEqual(dp._indexer_bytes('940 MB'), 940 * 1024 ** 2)
        for blank in ('', None, ' ', 'n/a', 0, -5, True):
            self.assertIsNone(dp._indexer_bytes(blank), blank)

    def test_space_is_listed_only_when_the_worker_knows_it(self):
        with patch.object(dp, '_space_supported', return_value=True):
            self.assertIn('space', dp.debrid_status()['features'])
        with patch.object(dp, '_space_supported', return_value=False):
            self.assertNotIn('space', dp.debrid_status()['features'])
        # A worker that has never heard of the op answers 'unknown command': nothing is listed, so the
        # panel never sends a space request to it.
        with patch.object(dp, '_download_rpc', side_effect=RuntimeError('unknown command')):
            self.assertFalse(dp._space_supported())


class DebridNotifyTests(DebridTests):
    """Task 2 in docs/SPEC-downloads-augmentations.md: the dashboard's own tick tells Ryan over ntfy when
    a download ends. The topic lives in a private file this test writes; every POST is captured, none sent.
    """

    def setUp(self):
        DebridTests.setUp(self)
        self.ntfy_path = os.path.join(self.tmp.name, 'ntfy.json')
        self.posts, self.ntfy_ok = [], True
        self.p_ntfy = patch.object(dp, 'NTFY_CONFIG', self.ntfy_path)
        self.p_url = patch.object(dp.urllib.request, 'urlopen', side_effect=self._capture)
        self.p_ntfy.start()
        self.p_url.start()
        self.addCleanup(self.p_ntfy.stop)
        self.addCleanup(self.p_url.stop)
        self.addCleanup(setattr, dp, '_NTFY_CACHE', [0.0, None])

    class _Accepted:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _capture(self, request, timeout=None):
        self.posts.append(request)
        if not self.ntfy_ok:
            raise urllib.error.URLError('no route to ntfy')
        return self._Accepted()

    def ntfy_config(self, topic='fake-topic-value'):
        with open(self.ntfy_path, 'w') as f:
            json.dump({'server': 'https://ntfy.test', 'topic': topic}, f)

    @staticmethod
    def headers(request):
        return {str(k).lower(): v for k, v in request.header_items()}

    def nrow(self, **over):
        base = {'dest': dp.MOVIES_DIR, 'name': 'Dune.2021.mkv', 'kind': 'movie', 'title': 'Dune',
                'season': None, 'episode': None, 'started': 1, 'state': 'finished', 'bytes': 5 * 1024 ** 3,
                'total_bytes': 5 * 1024 ** 3, 'scanned': True}
        base.update(over)
        return base

    def tick(self, jobs=None, scan=True):
        """One tick of the real loop with a worker that answers the check with `jobs`."""
        answer = {'ok': True, 'jobs': jobs if jobs is not None else []}

        def call(payload, timeout=None):
            return answer

        with patch.object(dp, '_download_rpc', side_effect=call), \
             patch.object(dp, '_jellyfin_scan', return_value=scan), \
             patch.object(dp, '_debrid_prep_tick'):   # step 2 is another test's subject
            dp._debrid_tick()

    def test_the_library_having_it_sends_one_message(self):
        self.ntfy_config()
        self.write_state([self.nrow()])
        self.tick()
        self.tick()                     # a second tick, and a restart, must not tell it twice
        self.assertEqual(len(self.posts), 1)
        req = self.posts[0]
        head = self.headers(req)
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.full_url, 'https://ntfy.test/fake-topic-value')
        self.assertEqual(head['title'], 'Added: Dune')
        self.assertEqual(head['priority'], '2')
        self.assertEqual(head['click'], 'homelab://view/media')      # tapping it opens the Downloads panel
        self.assertEqual(head['x-sequence-id'], 'dl-Dune.2021.mkv')  # a later result replaces the earlier one
        body = req.data.decode('utf-8')
        self.assertIn('5 GB', body)
        self.assertIn('Movies', body)                            # the folder it landed in, as the panel writes it
        self.assertEqual(self.read_state()[0]['notified'], 'finished')   # the end it was told about

    def test_the_bytes_landing_is_not_yet_the_news(self):
        self.ntfy_config()
        self.write_state([self.nrow(scanned=False)])
        self.tick(scan=False)                   # Jellyfin refused the scan: the item is not in the library
        self.assertEqual(self.posts, [])
        self.tick(scan=True)
        self.assertEqual(len(self.posts), 1)    # the message waits for the library, like the panel does

    def test_a_download_stopped_by_the_person_is_silent(self):
        self.ntfy_config()
        self.write_state([self.nrow(state='stopped', scanned=True)])
        self.tick()
        self.tick()
        self.assertEqual(self.posts, [])
        self.assertEqual(self.read_state()[0]['notified'], 'stopped')   # silence, marked so it stays silence

    def test_a_failure_says_the_same_sentence_the_panel_shows(self):
        self.ntfy_config()
        self.write_state([self.nrow(state='failed', error='peer closed connection without sending complete '
                                   'message body', scanned=False)])
        self.tick()
        self.assertEqual(len(self.posts), 1)
        head = self.headers(self.posts[0])
        self.assertEqual(head['title'], 'Download failed: Dune')
        self.assertEqual(head['priority'], '3')                       # louder than an added item
        self.assertIn('peer closed connection', self.posts[0].data.decode('utf-8'))

    def test_a_season_grab_speaks_once_for_the_whole_grab(self):
        self.ntfy_config()
        grab = {'id': 'rd-9'}
        self.write_state([self.nrow(name='S02E01.mkv', title='Severance S02E01', kind='show', season=2,
                                   show_dir='Severance', debrid=dict(grab)),
                          self.nrow(name='S02E02.mkv', title='Severance S02E02', kind='show', season=2,
                                   show_dir='Severance', debrid=dict(grab)),
                          {'dest': dp.SHOWS_DIR, 'name': 'rd-9', 'kind': 'show', 'title': 'Severance season 2',
                           'season': 2, 'state': 'preparing', 'debrid': dict(grab)}])
        self.tick()                       # the torrent is still handing files over
        self.tick()
        self.assertEqual(self.posts, [])
        self.write_state(self.read_state()[:2])   # every file of the grab has now been handed over
        self.tick()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.headers(self.posts[0])['title'], 'Added: Severance season 2')
        self.assertIn('2 files', self.posts[0].data.decode('utf-8'))

    def test_a_partial_grab_says_what_arrived(self):
        self.ntfy_config()
        grab = {'id': 'rd-9'}
        self.write_state([self.nrow(name='S02E01.mkv', title='Severance S02E01', kind='show', season=2,
                                   show_dir='Severance', debrid=dict(grab)),
                          self.nrow(name='S02E02.mkv', title='Severance S02E02', kind='show', season=2,
                                   show_dir='Severance', state='failed', error='link refused', scanned=False,
                                   debrid=dict(grab))])
        self.tick()
        self.assertEqual(len(self.posts), 1)
        body = self.posts[0].data.decode('utf-8')
        self.assertIn('1 of 2 files', body)
        self.assertIn('link refused', body)

    def test_a_dead_topic_says_it_once_and_the_tick_keeps_running(self):
        self.ntfy_config()
        self.write_state([self.nrow()])
        self.ntfy_ok = False
        for _ in range(8):
            self.tick()                   # the tick never stops because of a message
        row = self.read_state()[0]
        self.assertEqual(len(self.posts), dp.NTFY_TRIES)   # retried up to the cap, then left alone
        self.assertEqual(row['notify_tries'], dp.NTFY_TRIES)
        self.assertEqual(row['notify_error'], 'the message did not reach ntfy')
        self.assertNotIn('notified', row)
        self.tick()
        self.assertEqual(len(self.posts), dp.NTFY_TRIES)   # the row left the queue: no more tries

    def test_a_non_ascii_title_goes_as_an_encoded_header(self):
        self.ntfy_config()
        self.write_state([self.nrow(name='\u00c4\u00f1\u00f5.mkv', title='\u00c4\u00f1\u00f5')])
        self.tick()
        self.assertEqual(len(self.posts), 1)
        title = self.headers(self.posts[0])['title']
        self.assertTrue(title.startswith('=?UTF-8?B?'))            # raw UTF-8 cannot ride a header
        decoded = base64.b64decode(title[len('=?UTF-8?B?'):-len('?=')]).decode('utf-8')
        self.assertEqual(decoded, 'Added: Äñõ')      # and it still says the same thing

    def test_the_topic_never_leaves_the_backend_and_the_flag_needs_the_file(self):
        self.ntfy_config(topic='secret-topic-value')
        self.write_state([self.nrow()])
        self.tick()
        answer = json.dumps(dp.debrid_status(), default=str)
        self.assertNotIn('secret-topic-value', answer)              # no topic in any panel answer
        self.assertNotIn('ntfy.test', answer)                     # nor the server
        self.assertIn('notify', dp.debrid_status()['features'])
        with patch.object(dp, 'NTFY_CONFIG', os.path.join(self.tmp.name, 'absent.json')):
            dp._NTFY_CACHE = [0.0, None]
            self.assertNotIn('notify', dp.debrid_status()['features'])   # no file: no flag, nothing to send


class DebridCachedTests(DebridTests):
    """Task 3 in docs/SPEC-downloads-augmentations.md: the search answer says which results TorBox already
    has ('Ready now') and marks one suggested pick. The TorBox call is captured here; the live answer shape
    is written up in docs/SPEC-search-via-debrid.md."""

    HASH = 'dd8255ecdc7ca55fb0bbf81323d87062db1f6d1f'

    def setUp(self):
        DebridTests.setUp(self)
        dp.RESULTS.clear()
        self.addCleanup(dp.RESULTS.clear)

    def hit(self, title, seeders=10, size='1.1 GB', info_hash=HASH):
        return {'guid': 'https://idx/' + title, 'indexerId': 1, 'title': title, 'protocol': 'torrent',
                'seeders': seeders, 'size': size, 'infoHash': info_hash}

    def search(self, rows, cached=(), broken=None, key='fake-torbox-key'):
        """One search: Prowlarr faked, TorBox's cached check captured. `cached` is what TorBox is made to
        answer that it already holds; `broken` makes the check fail instead (a missing key, a timeout)."""
        hits = []

        def fake(method, path, fields=(), files=(), timeout=20, json_body=None):
            hits.append({'method': method, 'path': path, 'timeout': timeout})
            if broken:
                raise broken
            have = {h: {'name': 'x', 'size': 1, 'hash': h} for h in cached}
            return {'success': True, 'error': None, 'detail': '', 'data': have}

        with patch.object(dp, 'prowlarr', return_value=rows), \
             patch.object(dp, 'torbox', side_effect=fake), \
             patch.object(dp, '_torbox_key', return_value=key):   # 'the service is set up here'
            return dp.search('dune', 'torrent'), hits

    def hashes_asked_about(self, hit):
        return [h for h in hit['path'].split('hash=')[1].split('?')[0].split(',') if h]

    def test_the_check_runs_once_for_the_whole_search(self):
        rows, hits = self.search([self.hit('Dune A', info_hash=self.HASH),
                                  self.hit('Dune B', info_hash='0123456789abcdef0123456789abcdef01234567'),
                                  self.hit('Dune C', info_hash='abcdef0123456789abcdef0123456789abcdef')],
                                cached=[self.HASH])
        self.assertEqual(len(hits), 1)                       # one batch call, not one per result
        self.assertEqual(hits[0]['method'], 'GET')
        self.assertIn('/torrents/checkcached', hits[0]['path'])
        self.assertEqual(len(self.hashes_asked_about(hits[0])), 3)
        out = {r['title']: r['cached'] for r in rows}
        self.assertEqual(out, {'Dune A': True, 'Dune B': False, 'Dune C': False})

    def test_a_hashless_result_is_never_fetched_just_to_ask(self):
        rows, hits = self.search([self.hit('Dune A'), self.hit('No hash here', info_hash=None)],
                                cached=[self.HASH])
        self.assertEqual(self.hashes_asked_about(hits[0]), [self.HASH])   # only the hash it was given
        self.assertEqual([(r['title'], r['cached']) for r in rows],
                         [('Dune A', True), ('No hash here', None)])      # unknown, not a no

    def test_a_check_that_did_not_answer_leaves_the_search_normal(self):
        rows, hits = self.search([self.hit('Dune A', info_hash=self.HASH, seeders=4),
                                  self.hit('Dune B.2160p', info_hash='0123456789abcdef0123456789abcdef01234567')],
                                broken=RuntimeError('TorBox did not answer in time'))
        self.assertEqual([r['cached'] for r in rows], [None, None])   # everything unknown
        self.assertEqual([r for r in rows if r.get('suggested')][0]['title'], 'Dune B.2160p')  # still a pick
        self.assertTrue(all(r['title'] in rows[0]['title'] or r['title'] for r in rows))

    def test_the_pick_prefers_4k_then_seeders(self):
        rows, _ = self.search([self.hit('Dune 1080p', seeders=900, info_hash=self.HASH),
                               self.hit('Dune 2160p', seeders=3, info_hash='0123456789abcdef0123456789abcdef01234567')],
                              cached=[self.HASH, '0123456789abcdef0123456789abcdef01234567'])
        picks = [r['title'] for r in rows if r.get('suggested')]
        self.assertEqual(picks, ['Dune 2160p'])                     # resolution beats raw seeders
        self.assertEqual(sum(1 for r in rows if r.get('suggested')), 1)

    def test_a_cam_or_ts_is_never_the_pick(self):
        rows, _ = self.search([self.hit('Dune CAM 2160p', seeders=900, info_hash=self.HASH),
                               self.hit('Dune.TS.1080p', seeders=20, info_hash='0123456789abcdef0123456789abcdef01234567'),
                               self.hit('Dune 1080p', seeders=12, info_hash='abcdef0123456789abcdef0123456789abcdef')],
                              cached=[self.HASH, '0123456789abcdef0123456789abcdef01234567',
                                      'abcdef0123456789abcdef0123456789abcdef'])
        self.assertEqual([r['title'] for r in rows if r.get('suggested')], ['Dune 1080p'])

    def test_a_release_bigger_than_the_cap_is_never_the_pick(self):
        rows, _ = self.search([self.hit('Dune remux 2160p', seeders=900, size='120 GB', info_hash=self.HASH),
                               self.hit('Dune 1080p', seeders=5, info_hash='0123456789abcdef0123456789abcdef01234567')],
                              cached=[self.HASH, '0123456789abcdef0123456789abcdef01234567'])
        self.assertEqual([r['title'] for r in rows if r.get('suggested')], ['Dune 1080p'])

    def test_a_known_answer_of_no_leaves_the_search_without_a_pick(self):
        rows, _ = self.search([self.hit('Dune A', seeders=900), self.hit('Dune B.2160p', seeders=800)], cached=[])
        self.assertEqual([r['cached'] for r in rows], [False, False])   # all known, none ready
        self.assertEqual([r for r in rows if r.get('suggested')], [])

    def test_a_batch_stays_inside_the_documented_cap(self):
        rows, hits = self.search([self.hit('Dune %02d 1080p' % i, info_hash='%040x' % i) for i in range(120)])
        self.assertEqual(len(hits), 1)
        self.assertLessEqual(len(self.hashes_asked_about(hits[0])), dp.CACHED_MAX)   # about 100 per call
        self.assertEqual(len(rows), 60)                                          # and 60 results at most

    def test_the_cached_flag_needs_a_torbox_key(self):
        with patch.object(dp, '_torbox_key', return_value=None):
            self.assertNotIn('cached', dp.debrid_status()['features'])
        rows, hits = self.search([self.hit('Dune A')], key=None)
        self.assertEqual(hits, [])                     # no key: no call, so no request to a service that is off
        self.assertEqual([r['cached'] for r in rows], [None])
        with patch.object(dp, '_torbox_key', return_value='fake-key'):
            self.assertIn('cached', dp.debrid_status()['features'])

    @unittest.skipUnless(os.path.exists(dp.TORBOX), "needs Ryan's TorBox key file (only LXC 104 has it)")
    def test_the_live_answer_shape(self):
        """The one live read the spec asks for: an uncached hash is absent, and the call answers well inside
        the 3 s budget."""
        self.assertTrue(dp._torbox_key())
        flags = dp._cached_flags(['0123456789abcdef0123456789abcdef01234567'])
        self.assertIsInstance(flags, set)         # a set of cached hashes, empty when none are cached
        self.assertEqual(flags, set())
