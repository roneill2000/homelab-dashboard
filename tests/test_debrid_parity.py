"""The dashboard's rules and the n100 worker's rules must agree.

The dashboard (backend/download_proxy.py) checks a request before it sends it, for a clear
message. The worker (scripts/homelab_download_lib.py) checks again on .3, where the share is
mounted, and has the final word. When the two disagree, a download either fails late or
reaches the share with a path nobody agreed to. These tests hold the two sides level.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

import download_proxy as dp           # dashboard side (backend/)
import homelab_download_lib as lib    # worker side (scripts/)


class SharedRoot(unittest.TestCase):
    """One temporary copy of the share shape, pointed at by both sides."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.movies = os.path.join(root, 'Movies')
        self.shows = os.path.join(root, 'Shows')
        os.makedirs(self.movies)
        os.makedirs(os.path.join(self.shows, 'Breaking Bad'))
        self.link = os.path.join(self.shows, 'link-to-movies')
        os.symlink(self.movies, self.link)
        self.patches = [patch.object(dp, 'MOVIES_DIR', self.movies), patch.object(dp, 'SHOWS_DIR', self.shows),
                        patch.object(lib, 'SHARE_ROOT', root),
                        patch.object(lib, 'MOVIES_DIR', self.movies), patch.object(lib, 'SHOWS_DIR', self.shows)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)

    def verdict(self, side, func, value):
        """('ok', returned value) or ('refused', message)."""
        try:
            return ('ok', func(value))
        except (ValueError, lib.BadDownload) as exc:
            return ('refused', str(exc))

    # ----------------------------------------------------------------- lists ---
    def test_the_two_sides_allow_the_same_media_extensions(self):
        self.assertEqual(set(dp.MEDIA_EXT), lib.MEDIA_EXT)
        for ext in ('.mkv', '.mp4', '.webm', '.epub'):
            self.assertIn(ext, lib.MEDIA_EXT)
        for ext in ('.txt', '.nfo', '.srt', '.exe', '.part'):
            self.assertNotIn(ext, lib.MEDIA_EXT)

    def test_the_two_sides_refuse_the_same_bad_characters(self):
        self.assertEqual(dp._NAME_BAD.pattern, lib.NAME_BAD.pattern)
        self.assertEqual(dp._NAME_BAD.pattern, lib.SHOW_DIR_BAD.pattern)

    # ----------------------------------------------------------------- dests ---
    def test_destination_verdicts_are_identical(self):
        cases = [self.movies, self.movies + '/', self.movies + '//', self.shows + '/Breaking Bad',
                 self.shows + '/New Show', self.shows + '/Breaking Bad/Season 1', self.shows,
                 self.movies + '/../etc', self.shows + '/../Movies', self.shows + '/..', '/etc', '/',
                 'Movies', './Movies', self.movies + '\\..\\..\\etc', self.movies + '/../../root',
                 '', ' ', self.movies + '/' + 'x' * 200, self.movies + '/Movies']
        for dest in cases:
            back, worker = self.verdict('dashboard', dp._safe_dest, dest), self.verdict('worker', lib.safe_dest, dest)
            self.assertEqual(back[0], worker[0], '%r: dashboard %s, worker %s' % (dest, back[0], worker[0]))
            if back[0] == 'ok':
                self.assertEqual(back[1], worker[1], repr(dest))

    def test_a_symlinked_destination_is_refused_only_on_the_worker(self):
        # Only .3 can see the share, so only the worker can prove a path is not a symlink.
        # The dashboard accepts it; the worker refuses it. That is the agreed split, pinned here.
        link = self.link  # inside Shows, but points outside: only realpath can tell
        self.assertEqual(self.verdict('dashboard', dp._safe_dest, link)[0], 'ok')
        self.assertEqual(self.verdict('worker', lib.safe_dest, link)[0], 'refused')

    # ----------------------------------------------------------------- names ---
    def test_media_file_name_verdicts_are_identical(self):
        # The dashboard names only media downloads; the worker's media rule is media_name.
        cases = ['Memento.2000.BluRay.1080p.mkv', 'a.mkv', 'Memento 2000.mkv', 'x.webm', 'book.epub',
                 'Memento.mkv/', '../x.mkv', 'a/../b.mkv', 'a/b.mkv', 'a\\b.mkv', 'a:b.mkv', 'a*b.mkv',
                 'a\nb.mkv', '.', '..', '-x.mkv', '.hidden.mkv', 'no_ext', 'notes.txt', 'file.exe',
                 '.mkv', 'x..mkv', 'x' * 201 + '.mkv', '', '   ', 5, None]
        for name in cases:
            back, worker = self.verdict('dashboard', dp._safe_name, name), self.verdict('worker', lib.media_name, name)
            self.assertEqual(back[0], worker[0], '%r: dashboard %s, worker %s' % (name, back[0], worker[0]))

    def test_an_id_only_link_never_reaches_the_worker_unnamed(self):
        # A TorBox nexus link is /dld/<uuid>?token=..., so the URL names nothing. The dashboard
        # discovers the name from the server's headers (download_proxy._discover_name) and sends it
        # as a normal media name; the worker checks that name like any other. Neither side starts
        # a download from an unnamed link.
        url = 'https://nexus-228.example/dld/4fb4e907-d23c-4705-bfdb-9b5d0723efec?token=2559c10a'
        self.assertEqual(self.verdict('dashboard', dp._safe_name, dp._name_from_url(url))[0], 'refused')
        self.assertEqual(self.verdict('worker', lib.media_name, lib.name_from_url(url))[0], 'refused')
        with self.assertRaises(lib.BadDownload):
            lib.start_debrid(url, self.movies, '')
        named = 'Dune.2021.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv'
        self.assertEqual(self.verdict('worker', lib.media_name, named), self.verdict('dashboard', dp._safe_name, named))

    # ------------------------------------------------------------- show dirs ---
    def test_show_folder_verdicts_are_identical(self):
        cases = ['Breaking Bad', '  The   Office  ', 'Severance (2022)', 'A,B & "C"', 'The-Office',
                 'x' * 81, 'x' * 80, '.', '..', '   ', 'a/b', 'a\\b', 'a:b', '', None, 5]
        for title in cases:
            back, worker = self.verdict('dashboard', dp._safe_show_dir, title), self.verdict('worker', lib.safe_show_dir, title)
            self.assertEqual(back[0], worker[0], '%r: dashboard %s, worker %s' % (title, back[0], worker[0]))
            if back[0] == 'ok':
                self.assertEqual(back[1], worker[1], repr(title))

    def test_the_worker_also_runs_plain_files_the_library_never_takes(self):
        # The shell front end downloads any file type (books, subs, a test file); the dashboard
        # offers media only. That split is deliberate: safe_name is path safety, media_name is the library.
        self.assertEqual(lib.safe_name('10Mb.dat'), '10Mb.dat')
        self.assertEqual(lib.safe_name('book.pdf'), 'book.pdf')
        with self.assertRaises(ValueError):
            dp._safe_name('10Mb.dat')
        with self.assertRaises(lib.BadDownload):
            lib.media_name('10Mb.dat')

    # --------------------------------------------------- end to end on paper ---
    def add(self, url, grounding=None):
        """Run the dashboard's add path and capture what it would send to the worker."""
        sent = {}

        def fake_rpc(payload, *args, **kwargs):
            sent.update(payload)
            return {'ok': True, 'unit': 'dl-' + payload['name'], 'state': 'downloading',
                    'dest': payload['dest'], 'name': payload['name'], 'message': 'started'}

        state_file = os.path.join(self.movies, 'debrid-parity.json')
        # Two units at a time is the worker's own limit (Task 4); this test checks every folder and name
        # the dashboard could send, so it is always given a free slot.
        with patch.object(dp, '_download_rpc', side_effect=lambda payload: fake_rpc(payload)), \
             patch.object(dp, 'DEBRID_STATE', state_file), \
             patch.object(dp, 'searxng_ground', return_value=grounding), \
             patch.object(dp, '_free_slots', lambda extra=0: 99):
            out = dp.add_debrid(url)
        return out, sent

    def test_every_destination_the_dashboard_sends_the_worker_accepts(self):
        urls = ['https://cdn.example/d/Memento.2000.BluRay.1080p.DTS-HD.5.1.AVC.HYBRID.mkv',
                'https://cdn.example/d/Breaking.Bad.S05E02.HDTV.x264.mkv',
                'https://cdn.example/d/Some.Show.2019.1080p.mkv',
                'https://cdn.example/d/1917.2019.1080p.mkv',
                'https://cdn.example/d/2001.A.Space.Odyssey.1968.1080p.mkv',
                'https://cdn.example/d/Season.of.the.Witch.2011.720p.mkv',
                'https://cdn.example/d/Severance.S02.2022.BluRay.1080p.mkv',
                'https://cdn.example/d/The%20Office%20S01E01.mkv',
                'https://nyk7-4.download.real-debrid.com/d/ABCDEF/Memento%202000.mkv']
        for url in urls:
            for grounding in (None, 'show', 'movie'):
                out, sent = self.add(url, grounding)
                self.assertTrue(out['ok'], url)
                self.assertEqual(lib.safe_dest(sent['dest']), sent['dest'], '%s %s: worker refuses this folder' % (url, grounding))
                self.assertEqual(lib.safe_name(sent['name']), sent['name'], '%s: worker refuses this name' % url)
                self.assertIn(sent['cmd'], ('start',))

    def test_the_dashboard_refuses_what_it_can_see_itself(self):
        # No SSH is ever run from these tests: _download_rpc explodes if anything tries.
        bad = ('https://cdn.example/d/notes.txt', 'http://cdn.example/d/x.mkv',
               'https://cdn.example/d/x.mkv && echo pwned', 'not a url', '  ', 'https:///x.mkv',
               'https://', 'ftp://cdn.example/x.mkv', 'https://cdn.example', 'https://cdn.example/',
               'https://cdn.example/d/x.mkv\nGET /evil', None, 5, 'https://' + 'a' * 4001)
        with patch.object(dp, '_download_rpc', side_effect=AssertionError('the dashboard ran ssh')) as rpc:
            for url in bad:
                with self.assertRaises(ValueError, msg=repr(url)):
                    dp.add_debrid(url)
        rpc.assert_not_called()

    def test_url_shape_verdicts_are_identical(self):
        """Structure: same scheme, host and path rules. Only the worker adds the LAN lookup."""
        cases = ['https://cdn.example/d/x.mkv', 'https://cdn.example', 'https://cdn.example/', 'https:///x.mkv',
                 'http://cdn.example/x.mkv', 'ftp://cdn.example/x.mkv', '', '  ', 'not a url', None, 5,
                 'https://cdn.example/d/x.mkv?a=1&b=2', 'https://cdn.example/d/x.mkv#frag',
                 "https://cdn.example/d/it's.mkv", 'https://cdn.example/d/x.mkv && echo pwned',
                 'https://cdn.example/d/..%2Fx.mkv', 'https://cdn.example/d/a/../b.mkv',
                 'https://' + 'a' * 4001, 'https://localhost:8096/d/x.mkv', 'https://192.168.0.2/d/x.mkv']
        for url in cases:
            back = self.verdict('dashboard', dp._safe_url, url)[0]
            worker = self.verdict('worker', lambda u: lib.safe_url(u, resolve=False), url)[0]
            self.assertEqual(back, worker, '%r: dashboard %s, worker %s' % (url, back, worker))

    def test_a_host_that_resolves_to_the_lan_is_refused_only_by_the_worker(self):
        # A LAN name or number is refused by both sides. A name that only DNS can place
        # inside the LAN is refused on .3, where the lookup is trustworthy.
        sent = {}

        def fake(payload):
            sent.update(payload)
            return {'ok': True, 'unit': 'dl-x.mkv', 'state': 'downloading', 'dest': payload['dest'],
                    'name': payload['name'], 'message': 'started'}

        with patch.object(dp, '_download_rpc', side_effect=fake), \
             patch.object(dp, 'DEBRID_STATE', os.path.join(self.movies, 'parity.json')):
            dp.add_debrid('https://blocked.example/d/x.mkv')
        with patch.object(lib.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('192.168.0.20', 443))]):
            self.assertTrue(lib.private_host('blocked.example'))
            with self.assertRaises(lib.BadDownload):
                lib.safe_url(sent['url'])


if __name__ == '__main__':
    unittest.main()
