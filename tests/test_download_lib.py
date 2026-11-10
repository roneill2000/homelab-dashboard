"""Rules the n100 download worker enforces (scripts/homelab_download_lib.py).

Nothing here touches the share, systemd, aria2c, or DNS: the state machine is a pure
function, file and log checks run in a temporary folder, and log fixtures are copied
from real logs on .3.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import homelab_download_lib as lib

URL = 'https://nyk7-4.download.real-debrid.com/d/abcdef/Memento.2000.BluRay.1080p.mkv'

# Copied from the real debrid log on .3 (dl-Memento...log), trimmed.
ARIA2_LOG = """start aria2c -> /mnt/unraid/Movies/Memento.mkv
 *** Download Progress Summary as of Mon Nov  9 23:36:02 2026 ***
===============================================================================
[#a13493 264MiB/31GiB(0%) CN:16 DL:164MiB ETA:3m14s]
[#a13493 4.7GiB/31GiB(15%) CN:16 DL:274MiB ETA:1m39s]
[#a13493 9.5GiB/31GiB(30%) CN:16 DL:276MiB]
[#a13493|OK  |   251MiB/s|/mnt/unraid/Movies/Memento.mkv]

Status Legend:
(OK):download completed.
DONE 2026-11-09 23:38:45 /mnt/unraid/Movies/Memento.mkv
"""

# Same shape, but still running: the newest line is an aria2c summary.
ARIA2_RUNNING = """start aria2c -> /mnt/unraid/Movies/Memento.mkv
 *** Download Progress Summary as of Mon Nov  9 23:36:02 2026 ***
===============================================================================
[#a13493 264MiB/31GiB(0%) CN:16 DL:164MiB ETA:3m14s]
[#a13493 4.7GiB/31GiB(15%) CN:16 DL:274MiB ETA:1m39s]
[#a13493 9.5GiB/31GiB(30%) CN:16 DL:276MiB]
"""

# A connection error line must not erase the progress in the line before it.
ARIA2_ERROR = """start aria2c -> /mnt/unraid/Movies/x.mkv
[#a13493 4.7GiB/31GiB(15%) CN:16 DL:274MiB ETA:1m39s]
[ERROR] Download aborted. Reason: No existing server-side file found.
"""

ARIA2_FAILED = """start aria2c -> /mnt/unraid/Movies/x.mkv
[#a13493 4.7GiB/31GiB(15%) CN:16 DL:274MiB ETA:1m39s]
FAILED 2026-11-09 23:38:45 aria2c exited with code 2
"""

# Copied from the real rate-limited wget log on .3 (dl-10Mb.dat.log), trimmed.
WGET_LOG = """--2026-11-02 20:44:06--  https://proof.ovh.net/files/10Mb.dat
Resolving proof.ovh.net (proof.ovh.net)... 141.95.207.211, 2001:41d0:242:d300::
Connecting to proof.ovh.net (proof.ovh.net)|141.95.207.211|:443... connected.
HTTP request sent, awaiting response... 200 OK
Length: 10485760 (10M) [application/octet-stream]
Saving to: '/mnt/unraid/Movies/10Mb.dat.part'
     0K ........ ..                        100% 4.39M=2.3s
2026-11-02 20:44:09 (4.39 MB/s) - '/mnt/unraid/Movies/10Mb.dat.part' saved [10485760/10485760]
DONE 2026-11-02 20:44:09 /mnt/unraid/Movies/10Mb.dat
"""


class NameRules(unittest.TestCase):
    def test_good_names_pass_unchanged(self):
        for name in ('Memento.2000.BluRay.1080p.mkv', 'a.mkv', 'file.mp4', 'Memento 2000.mkv'):
            self.assertEqual(lib.safe_name(name), name)

    def test_bad_names_are_refused(self):
        bad = ['', '   ', '.', '..', 'Memento.mkv/', '..%2FMemento.mkv', 'a/../b.mkv', 'a\\b.mkv',
               '-x.mkv', '.hidden.mkv', 'a:b.mkv', 'a|b.mkv', 'a?b.mkv', 'a*b.mkv', 'a"b.mkv',
               'a\nb.mkv', 'a\tb.mkv', 'no_ext', '.mkv', 'x' * 201 + '.mkv']
        for name in bad:
            with self.assertRaises(lib.BadDownload, msg=repr(name)):
                lib.safe_name(name)
        for name in (None, 7, {}):
            with self.assertRaises(lib.BadDownload, msg=repr(name)):
                lib.safe_name(name)

    def test_the_media_library_only_takes_media_names(self):
        # safe_name allows any file type: the rate-limited wget path downloads books, subs, test files.
        # media_name is the media library rule, used when a debrid download is started.
        for name in ('Memento.mkv', 'a.webm', 'book.epub', 'x.1080p.mp4', '10Mb.dat', 'notes.txt'):
            self.assertEqual(lib.safe_name(name), name)
        for name in ('Memento.mkv', 'a.webm', 'book.epub'):
            self.assertEqual(lib.media_name(name), name)
        for name in ('10Mb.dat', 'book.pdf', 'subtitles.srt', 'notes.txt', 'cover.jpg'):
            with self.assertRaises(lib.BadDownload, msg=name):
                lib.media_name(name)
        for bad in ('.mkv', '..mkv', 'x.mkv/'):
        # (x..mkv is legal on the share: the rule is about the extension, not about dots)
            with self.assertRaises(lib.BadDownload, msg=bad):
                lib.media_name(bad)

    def test_url_names_are_decoded_before_the_last_segment_is_taken(self):
        self.assertEqual(lib.name_from_url('https://h/d/Memento%202000.mkv'), 'Memento 2000.mkv')
        # %2F becomes a separator before the name is taken, so it cannot smuggle a path.
        self.assertEqual(lib.name_from_url('https://h/d/..%2Fescape.mkv'), 'escape.mkv')
        self.assertEqual(lib.name_from_url('https://h/a/b/../c.mkv'), 'c.mkv')
        with self.assertRaises(lib.BadDownload):  # a NUL from %00 is refused by the name rules
            lib.safe_name(lib.name_from_url('https://h/d/%00.mkv'))
        for url in ('https://h/d/%2F..%2Fetc.mkv', 'https://h/d/%2Fx.mkv', 'https://h/d/a/b/c.mkv'):
            name = lib.name_from_url(url)
            self.assertNotIn('/', name, url)
            self.assertNotIn(chr(92), name, url)

    def test_show_folder_names(self):
        self.assertEqual(lib.safe_show_dir('Breaking Bad'), 'Breaking Bad')
        self.assertEqual(lib.safe_show_dir('  The   Office  '), 'The Office')
        self.assertEqual(lib.safe_show_dir('Severance (2022)'), 'Severance (2022)')
        for bad in ('', '   ', '.', '..', '...', 'a/b', 'a\\b', 'a:b', 'x' * 81, None, 5):
            with self.assertRaises(lib.BadDownload, msg=repr(bad)):
                lib.safe_show_dir(bad)


class UrlRules(unittest.TestCase):
    def test_good_url_passes_unchanged(self):
        with patch.object(lib, 'private_host', return_value=False):
            self.assertEqual(lib.safe_url('  ' + URL + '  '), URL)

    def test_must_be_a_direct_https_link(self):
        for bad in ('', None, 5, 'http://cdn.example/f.mkv', 'https://cdn.example',
                    'https://cdn.example/', 'ftp://cdn.example/f.mkv', 'https:///f.mkv', 'https://'):
            with self.assertRaises(lib.BadDownload, msg=repr(bad)):
                lib.safe_url(bad)
        with self.assertRaises(lib.BadDownload):
            lib.safe_url('https://cdn.example/' + 'a' * 4000 + '.mkv')

    def test_lan_addresses_are_refused_without_any_lookup(self):
        for url in ('https://localhost:8096/f.mkv', 'https://pi.hole/f.mkv'):
            with self.assertRaises(lib.BadDownload, msg=url):
                lib.safe_url(url)
        for url in ('https://127.0.0.1/f.mkv', 'https://192.168.0.2/f.mkv', 'https://10.0.0.5/f.mkv',
                    'https://169.254.169.254/f.mkv', 'https://172.16.1.1/f.mkv', 'https://100.64.0.1/f.mkv'):
            with patch.object(lib.socket, 'getaddrinfo', side_effect=AssertionError('must not look up a LAN host')):
                with self.assertRaises(lib.BadDownload, msg=url) as caught:
                    lib.safe_url(url)
            self.assertIn('LAN', str(caught.exception))
        with self.assertRaises(lib.BadDownload):
            lib.safe_url('https://[::1]/f.mkv')

    def test_a_host_that_resolves_to_a_lan_address_is_refused(self):
        with patch.object(lib.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('192.168.0.20', 443))]):
            self.assertTrue(lib.private_host('blocked.example'))
            with self.assertRaises(lib.BadDownload):
                lib.safe_url('https://blocked.example/f.mkv')

    def test_a_public_host_passes(self):
        with patch.object(lib.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('141.95.207.211', 443))]):
            self.assertFalse(lib.private_host('cdn.example'))
            self.assertEqual(lib.safe_url('https://cdn.example/f.mkv'), 'https://cdn.example/f.mkv')

    def test_dns_failure_is_refused_not_crashed(self):
        def boom(*args, **kwargs):
            raise OSError('Name or service not known')
        with patch.object(lib.socket, 'getaddrinfo', side_effect=boom):
            with self.assertRaises(lib.BadDownload) as caught:
                lib.safe_url('https://gone.example/f.mkv')
        self.assertIn('does not resolve', str(caught.exception))

    def test_unresolved_host_is_refused_by_name(self):
        # The worker resolves with the network's own resolver (a Pi hole). A host it cannot
        # place is refused with a message that says so, rather than started and failed later.
        lib._DNS_CACHE.clear()
        try:
            with patch.object(lib, '_lookup', side_effect=OSError):
                with self.assertRaises(lib.BadDownload) as ctx:
                    lib.safe_url('https://no-such-host.invalid/x.mkv')
            self.assertIn('does not resolve', str(ctx.exception))
        finally:
            lib._DNS_CACHE.clear()

    def test_lookups_are_timed_out_and_cached(self):
        seen = {}

        def fake(host, port, **kwargs):
            import socket as s
            seen['timeout'] = s.getdefaulttimeout()
            seen['calls'] = seen.get('calls', 0) + 1
            return [(2, 1, 6, '', ('141.95.207.211', 443))]

        lib._DNS_CACHE.pop('fresh.example', None)
        with patch.object(lib.socket, 'getaddrinfo', side_effect=fake):
            self.assertFalse(lib.private_host('fresh.example'))
            self.assertFalse(lib.private_host('fresh.example'))
        self.assertEqual(seen['calls'], 1)
        self.assertEqual(seen['timeout'], 2.0)  # a dead resolver cannot stall the worker
        self.assertIsNone(__import__('socket').getdefaulttimeout())

    def test_cache_expires(self):
        lib._DNS_CACHE['aging.example'] = (__import__('time').time() - lib._DNS_TTL - 1, True)
        with patch.object(lib.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('141.95.207.211', 443))]):
            self.assertFalse(lib.private_host('aging.example'))


class DestRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.movies = os.path.join(root, 'mnt', 'unraid', 'Movies')
        self.shows = os.path.join(root, 'mnt', 'unraid', 'Shows')
        os.makedirs(self.movies)
        os.makedirs(os.path.join(self.shows, 'Breaking Bad'))
        os.symlink(self.movies, os.path.join(root, 'link-to-movies'))
        self.patches = [patch.object(lib, 'SHARE_ROOT', os.path.join(root, 'mnt', 'unraid')),
                        patch.object(lib, 'MOVIES_DIR', self.movies), patch.object(lib, 'SHOWS_DIR', self.shows)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)

    def test_allowed_destinations(self):
        self.assertEqual(lib.safe_dest(self.movies), self.movies)
        self.assertEqual(lib.safe_dest(self.movies + '/'), self.movies)
        self.assertEqual(lib.safe_dest(self.shows + '/Breaking Bad'), self.shows + '/Breaking Bad')
        # a folder deeper inside a show is still inside the approved root
        self.assertEqual(lib.safe_dest(self.shows + '/Breaking Bad/Season 1'), self.shows + '/Breaking Bad/Season 1')

    def test_refused_destinations(self):
        bad = ['', None, 3, 'Movies', './Movies', 'mnt/unraid/Movies', self.shows, self.shows + '/..',
               self.movies + '/../etc', '/mnt/unraid', '/', '/etc', self.movies + '/../../root',
               self.shows + '/Breaking Bad/../../..', self.movies + '\\..\\..\\etc', self.movies + '\x00',
               self.movies + '/' + 'x' * 200, self.shows + '/../Movies']
        for dest in bad:
            with self.assertRaises(lib.BadDownload, msg=repr(dest)):
                lib.safe_dest(dest)

    def test_the_operators_own_path_may_be_any_folder_on_the_share(self):
        # The rate-limited wget path writes outside the media library (Books, Audiobooks, a flat Shows).
        books = os.path.join(os.path.realpath(self.tmp.name), 'mnt', 'unraid', 'Books')
        os.makedirs(books)
        self.assertEqual(lib.share_dest(books), books)
        self.assertEqual(lib.share_dest(self.shows), self.shows)
        with self.assertRaises(lib.BadDownload):
            lib.safe_dest(books)          # the media library never writes there
        with self.assertRaises(lib.BadDownload):
            lib.safe_dest(self.shows)     # Shows itself is not a media library folder
        share = os.path.join(os.path.realpath(self.tmp.name), 'mnt', 'unraid')
        self.assertEqual(lib.share_dest(share), share)   # the operator may even aim at the share root
        with self.assertRaises(lib.BadDownload):
            lib.share_dest(os.path.dirname(share))       # ...but never outside it

    def test_a_symlink_is_not_a_destination(self):
        with self.assertRaises(lib.BadDownload):
            lib.safe_dest(os.path.join(os.path.realpath(self.tmp.name), 'link-to-movies'))


class StateMachine(unittest.TestCase):
    """decide_state(job, systemctl answer, file present, aria2 control file)."""

    def test_a_running_unit_is_downloading_whatever_the_record_says(self):
        for job in (None, {'state': 'complete'}, {'state': 'failed'}, {'state': 'running'}, {'state': 'stopped'}):
            self.assertEqual(lib.decide_state(job, 'active', True, True), 'downloading')
        self.assertEqual(lib.decide_state({'state': 'complete'}, 'activating', False, False), 'downloading')

    def test_a_file_alone_is_never_finished(self):
        # aria2c keeps <name>.aria2 while the file is incomplete.
        self.assertEqual(lib.decide_state({'state': 'complete'}, 'inactive', True, True), 'missing')
        self.assertEqual(lib.decide_state({'state': 'complete'}, 'inactive', False, False), 'missing')
        self.assertEqual(lib.decide_state({'state': 'complete'}, 'inactive', True, False), 'finished')
        self.assertEqual(lib.decide_state({'state': 'complete'}, 'failed', True, False), 'finished')

    def test_a_worker_that_never_reported_is_interrupted_not_finished(self):
        self.assertEqual(lib.decide_state({'state': 'running'}, 'inactive', True, False), 'interrupted')
        self.assertEqual(lib.decide_state({'state': 'running'}, 'inactive', True, True), 'interrupted')
        self.assertEqual(lib.decide_state(None, 'inactive', True, True), 'interrupted')

    def test_failed_records_keep_the_partial_visible(self):
        self.assertEqual(lib.decide_state({'state': 'failed', 'rc': 1}, 'inactive', False, False), 'failed')
        self.assertEqual(lib.decide_state({'state': 'failed', 'rc': 1}, 'inactive', True, True), 'failed')
        self.assertEqual(lib.decide_state({'state': 'failed', 'rc': 1}, 'inactive', True, False), 'interrupted')

    def test_stopped_stays_stopped(self):
        self.assertEqual(lib.decide_state({'state': 'stopped'}, 'inactive', True, True), 'stopped')
        self.assertEqual(lib.decide_state({'state': 'stopped'}, 'inactive', False, False), 'stopped')

    def test_no_record_at_all(self):
        self.assertEqual(lib.decide_state(None, 'inactive', True, False), 'present')
        self.assertEqual(lib.decide_state(None, 'inactive', False, False), 'unknown')
        self.assertEqual(lib.decide_state(None, 'unknown', True, False), 'present')


class FileAndJobState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.movies = os.path.join(root, 'Movies')
        self.logs = os.path.join(root, 'logs')
        self.jobs = os.path.join(root, 'jobs')
        for folder in (self.movies, self.logs, self.jobs):
            os.makedirs(folder)
        self.patches = [patch.object(lib, 'SHARE_ROOT', root),
                        patch.object(lib, 'MOVIES_DIR', self.movies),
                        patch.object(lib, 'SHOWS_DIR', os.path.join(root, 'Shows')),
                        patch.object(lib, 'LOG_DIR', self.logs), patch.object(lib, 'JOB_DIR', self.jobs),
                        patch.object(lib, 'mount_ok', return_value=True)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)
        self.name = 'Memento.2000.BluRay.1080p.mkv'
        self.unit = lib.unit_name(self.name)

    def test_unit_names_follow_the_shell_rules_this_fleet_already_used(self):
        self.assertEqual(self.unit, 'dl-' + self.name)
        self.assertEqual(len(lib.unit_name('x' * 200 + '.mkv')), 63)  # 'dl-' plus 60 characters
        for odd in ('a/b', 'a b', '../x', 'a;b'):
            unit = lib.unit_name(odd + '.mkv')
            self.assertTrue(unit.startswith('dl-'))
            self.assertRegex(unit, r'^dl-[A-Za-z0-9._-]+$')

    def test_only_a_unit_this_worker_could_make_is_accepted(self):
        for bad in ('', 'etc', 'dl-', 'dl-../x', 'dl-.hidden', 'dl-a/b', 'dl-x;rm', 'dl-' + 'y' * 90, None, 5):
            with self.assertRaises(lib.BadDownload, msg=repr(bad)):
                lib.safe_unit(bad)
        self.assertEqual(lib.job_path(self.unit), os.path.join(self.jobs, self.unit + '.json'))
        with self.assertRaises(lib.BadDownload):
            lib.job_path('../../escape')

    def make_file(self, folder, name, data=b'x'):
        with open(os.path.join(folder, name), 'wb') as f:
            f.write(data)

    def write_log(self, unit, text):
        with open(os.path.join(self.logs, unit + '.log'), 'w') as f:
            f.write(text)

    def test_file_state_reports_the_control_file(self):
        self.assertEqual(lib.file_state(self.movies, self.name), (False, 0, False))
        self.make_file(self.movies, self.name, b'x' * 1000)
        self.assertEqual(lib.file_state(self.movies, self.name), (True, 1000, False))
        self.make_file(self.movies, self.name + '.aria2', b'\0')
        self.assertEqual(lib.file_state(self.movies, self.name), (True, 1000, True))
        os.remove(os.path.join(self.movies, self.name + '.aria2'))
        self.assertEqual(lib.file_state(self.movies, self.name)[2], False)

    def test_a_record_is_written_atomically_and_read_back(self):
        job = lib.record_job(self.unit, 'complete', self.movies, self.name)
        self.assertEqual(job['state'], 'complete')
        self.assertFalse(os.path.exists(lib.job_path(self.unit) + '.tmp'))
        with open(lib.job_path(self.unit), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['dest'], self.movies)
        lib.write_job(self.unit, state='failed', error='aria2c exited with code 2')
        self.assertEqual(lib.read_job(self.unit)['state'], 'failed')

    def test_record_job_refuses_a_bad_dest_or_name(self):
        with self.assertRaises(lib.BadDownload):
            lib.record_job(self.unit, 'complete', '/etc', self.name)
        with self.assertRaises(lib.BadDownload):
            lib.record_job(self.unit, 'complete', self.movies, '../escape.mkv')

    def test_job_state_end_to_end_finished(self):
        self.make_file(self.movies, self.name, b'x' * 4096)
        lib.record_job(self.unit, 'complete', self.movies, self.name)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            st = lib.job_state(self.unit, self.movies, self.name)
        self.assertEqual((st['state'], st['bytes']), ('finished', 4096))

    def test_job_state_downloading_uses_aria2_own_summary(self):
        lib.record_job(self.unit, 'running', self.movies, self.name)
        self.write_log(self.unit, ARIA2_RUNNING)
        with patch.object(lib, 'unit_active', return_value='active'):
            st = lib.job_state(self.unit, self.movies, self.name)
        self.assertEqual(st['state'], 'downloading')
        self.assertEqual((st['total_bytes'], st['pct']), (31 * 1024 ** 3, 30))
        self.assertEqual(st['speed_bytes'], 276 * 1024 ** 2)
        self.assertNotIn('eta', st)  # the newest summary line has no ETA

    def test_a_running_unit_from_the_old_shell_script_still_reports_downloading(self):
        # Units started before job records existed: the running unit is the evidence.
        self.write_log('dl-legacy.dat', WGET_LOG)
        with patch.object(lib, 'unit_active', return_value='active'):
            st = lib.job_state('dl-legacy.dat')
        self.assertEqual(st['state'], 'downloading')
        self.assertEqual(st['total_bytes'], 10485760)

    def test_a_run_can_be_recorded_as_running_from_the_start(self):
        unit = lib.unit_name('10Mb.dat')
        lib.record_job(unit, 'running', self.movies, '10Mb.dat')
        self.assertEqual(lib.read_job(unit)['state'], 'running')

    def test_a_stopped_run_reports_the_partial_and_how_far_it_got(self):
        name, unit = '100Mb.dat', lib.unit_name('100Mb.dat')
        with open(os.path.join(self.movies, name + '.part'), 'wb') as f:
            f.write(b'x' * (12 * 1024 ** 2))
        lib.record_job(unit, 'stopped', self.movies, name, error='stopped by request')
        self.write_log(unit, WGET_LOG)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            st = lib.job_state(unit, self.movies, name)
        self.assertEqual((st['state'], st['bytes']), ('stopped', 12 * 1024 ** 2))
        self.assertEqual(st['total_bytes'], 10485760)

    def test_no_record_but_file_there_is_present(self):
        self.make_file(self.movies, self.name, b'x' * 10)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            self.assertEqual(lib.job_state(self.unit, self.movies, self.name)['state'], 'present')

    def test_download_without_a_dest_is_unknown_not_a_crash(self):
        with patch.object(lib, 'unit_active', return_value='unknown'):
            st = lib.job_state('dl-nothing.mkv')
        self.assertEqual(st['state'], 'unknown')
        self.assertIn('no record', st['error'])


class StatusLines(unittest.TestCase):
    """`homelab-download status` output for people and agents: one labelled line per download."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.logs = os.path.join(root, 'logs')
        self.jobs = os.path.join(root, 'jobs')
        self.movies = os.path.join(root, 'Movies')
        for folder in (self.logs, self.jobs, self.movies):
            os.makedirs(folder)
        self.patches = [patch.object(lib, 'SHARE_ROOT', root), patch.object(lib, 'LOG_DIR', self.logs),
                        patch.object(lib, 'JOB_DIR', self.jobs), patch.object(lib, 'MOVIES_DIR', self.movies),
                        patch.object(lib, 'SHOWS_DIR', os.path.join(root, 'Shows'))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)

    def log(self, name, text):
        with open(os.path.join(self.logs, name), 'w') as f:
            f.write(text)

    def test_a_unit_from_before_job_records_still_reports_from_its_log(self):
        # The old shell script left no job record: read the unit and its log instead of crashing.
        self.log('dl-10Mb.dat.log', WGET_LOG)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            lines = lib.status_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn('FINISHED', lines[0])
        self.assertIn('downloaded 10.0 MB of 10.0 MB (100%)', lines[0])

    def test_a_recorded_download_shows_state_then_bytes(self):
        name, unit = 'Memento.mkv', lib.unit_name('Memento.mkv')
        with open(os.path.join(self.movies, name), 'wb') as f:
            f.write(b'x' * 3000000)
        lib.record_job(unit, 'complete', self.movies, name)
        self.log(unit + '.log', ARIA2_LOG)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            lines = lib.status_lines()
        self.assertEqual((len(lines), lines[0].startswith(unit + ': FINISHED')), (1, True))
        # an aria2 log that finished: the file size is the evidence, no live progress
        self.assertIn('2.9 MB on disk', lines[0])

    def test_a_stopped_download_shows_the_labels_agents_parse(self):
        name, unit = '100Mb.dat', lib.unit_name('100Mb.dat')
        with open(os.path.join(self.movies, name + '.part'), 'wb') as f:
            f.write(b'x' * (12 * 1024 ** 2))
        lib.record_job(unit, 'stopped', self.movies, name, error='stopped by request')
        self.log(unit + '.log', WGET_LOG)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            line = lib.status_lines()[0]
        self.assertIn('STOPPED', line)
        self.assertIn('downloaded 10.0 MB of 10.0 MB (100%)', line)
        self.assertIn('speed 4.4 MB/s', line)

    def test_a_running_download_reports_progress_labels_agents_already_parse(self):
        name, unit = 'Memento.mkv', lib.unit_name('Memento.mkv')
        lib.record_job(unit, 'running', self.movies, name)
        self.log(unit + '.log', ARIA2_RUNNING)
        with patch.object(lib, 'unit_active', return_value='active'):
            lines = lib.status_lines()
        self.assertIn('DOWNLOADING', lines[0])
        self.assertIn('downloaded 9.5 GB of 31.0 GB (30%)', lines[0])
        self.assertIn('speed 276.0 MB/s', lines[0])

    def test_a_unit_name_that_is_not_ours_is_skipped_not_a_crash(self):
        self.log('not-a-unit.log', 'whatever\n')
        self.log('dl-fine.mkv.log', ARIA2_LOG)
        with patch.object(lib, 'unit_active', return_value='inactive'):
            self.assertEqual(len(lib.status_lines()), 1)

    def test_a_missing_log_folder_is_not_an_error(self):
        with patch.object(lib, 'LOG_DIR', os.path.join(self.tmp.name, 'missing-logs')):
            self.assertEqual(lib.status_lines(), [])

    def test_newest_log_comes_first(self):
        self.log('dl-old.mkv.log', ARIA2_LOG)
        self.log('dl-new.mkv.log', ARIA2_LOG)
        os.utime(os.path.join(self.logs, 'dl-old.mkv.log'), (1000, 1000))
        with patch.object(lib, 'unit_active', return_value='inactive'):
            lines = lib.status_lines()
        self.assertTrue(lines[0].startswith('dl-new.mkv.log'.replace('.log', '') + ':'))


class Progress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = os.path.join(os.path.realpath(self.tmp.name), 'logs')
        os.makedirs(self.logs)
        self.p = patch.object(lib, 'LOG_DIR', self.logs)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.addCleanup(self.tmp.cleanup)

    def write(self, unit, text):
        with open(os.path.join(self.logs, unit + '.log'), 'w') as f:
            f.write(text)

    def test_aria2_summary_lines_are_read_from_the_newest_one(self):
        unit = lib.unit_name('Memento.mkv')
        self.write(unit, ARIA2_RUNNING)
        prog = lib.progress_for(unit)
        self.assertEqual((prog['done_bytes'], prog['total_bytes'], prog['pct']),
                         (int(9.5 * 1024 ** 3), 31 * 1024 ** 3, 30))
        self.assertEqual(prog['speed_bytes'], 276 * 1024 ** 2)
        self.assertNotIn('eta', prog)

    def test_the_results_table_line_is_not_progress(self):
        unit = lib.unit_name('Table.mkv')
        self.write(unit, ARIA2_LOG.replace('DONE 2026-11-09 23:38:45', 'RUNNING 2026-11-09 23:38:45'))
        prog = lib.progress_for(unit)
        self.assertEqual((prog['total_bytes'], prog['pct']), (31 * 1024 ** 3, 30))

    def test_an_error_line_is_not_progress(self):
        unit = lib.unit_name('x.mkv')
        self.write(unit, ARIA2_ERROR)
        prog = lib.progress_for(unit)  # the summary line before the error still counts
        self.assertEqual((prog['total_bytes'], prog['pct']), (31 * 1024 ** 3, 15))
        self.assertEqual(prog['eta'], '1m39s')

    def test_done_or_failed_clears_progress(self):
        for name, text in (('y.mkv', ARIA2_LOG), ('z.mkv', ARIA2_FAILED)):
            unit = lib.unit_name(name)
            self.write(unit, text)
            self.assertEqual(lib.progress_for(unit), {}, name)

    def test_missing_or_unusable_log_gives_nothing(self):
        self.assertEqual(lib.progress_for('dl-notthere.mkv'), {})
        self.assertEqual(lib.progress_for('not-a-unit'), {})
        self.assertEqual(lib.progress_for('../escape'), {})
        self.write('dl-empty.mkv', 'no progress here\n')
        self.assertEqual(lib.progress_for('dl-empty.mkv'), {})

    def test_wget_rows_from_the_real_format(self):
        lines = [line for line in WGET_LOG.splitlines() if line]
        prog = lib.wget_progress(lines)
        self.assertEqual(prog['total_bytes'], 10485760)
        self.assertEqual(prog['pct'], 100)
        self.assertEqual(prog['done_bytes'], 10485760)
        self.assertEqual(prog['speed_bytes'], int(4.39 * 1024 ** 2))
        self.assertEqual(prog['eta'], '2.3s')

    def test_a_big_wget_download_shows_time_remaining(self):
        prog = lib.wget_progress(['Length: 36334897152 (34.0G) [application/octet-stream]',
                                  ' 12345678K .....     34% 25.0M 21m0s'])
        self.assertEqual(prog['pct'], 34)
        self.assertEqual(prog['done_bytes'], 12345678 * 1024)
        self.assertEqual(prog['eta'], '21m0s')

    def test_wget_progress_needs_its_own_lines(self):
        self.assertEqual(lib.wget_progress(['nothing at all here']), {})


class RpcInterface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.patches = [patch.object(lib, 'SHARE_ROOT', root),
                        patch.object(lib, 'MOVIES_DIR', os.path.join(root, 'Movies')),
                        patch.object(lib, 'SHOWS_DIR', os.path.join(root, 'Shows')),
                        patch.object(lib, 'LOG_DIR', os.path.join(root, 'logs')),
                        patch.object(lib, 'JOB_DIR', os.path.join(root, 'jobs'))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)

    def test_ping_and_version_gate(self):
        self.assertEqual(lib.rpc_reply({'v': lib.VERSION, 'cmd': 'ping'}),
                         {'ok': True, 'software': 'homelab-download', 'version': lib.VERSION})
        for req in ({}, {'v': 99, 'cmd': 'ping'}, {'cmd': 'ping'}, 'ping', None, 4, []):
            self.assertFalse(lib.rpc_reply(req)['ok'], req)
        self.assertIn('version', lib.rpc_reply({'v': 99, 'cmd': 'ping'})['error'])

    def test_unknown_command(self):
        out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'rm -rf /'})
        self.assertFalse(out['ok'])
        self.assertIn('unknown', out['error'])

    def test_start_refuses_a_bad_url_or_dest_before_starting_anything(self):
        bad = [{'url': 'http://h.example/x.mkv', 'dest': lib.MOVIES_DIR},
               {'url': URL, 'dest': '/etc'},
               {'url': URL, 'dest': lib.SHOWS_DIR},
               {'url': URL, 'dest': lib.MOVIES_DIR, 'name': '../escape.mkv'},
               {'url': URL, 'dest': lib.MOVIES_DIR, 'name': 'notes.txt'},
               {'url': '', 'dest': lib.MOVIES_DIR}]
        with patch.object(lib.subprocess, 'run') as run, patch.object(lib, 'private_host', return_value=False):
            for item in bad:
                out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'start', **item})
                self.assertFalse(out['ok'], item)
                self.assertIn('error', out)
        run.assert_not_called()  # nothing was started

    def test_start_answer_shape(self):
        info = {'ok': True, 'unit': 'dl-x.mkv', 'state': 'downloading', 'dest': lib.MOVIES_DIR,
                'name': 'x.mkv', 'message': 'started dl-x.mkv'}
        with patch.object(lib, 'start_debrid', return_value=info):
            out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'start', 'url': URL, 'dest': lib.MOVIES_DIR, 'name': 'x.mkv'})
        self.assertEqual((out['ok'], out['unit'], out['state'], out['name']), (True, 'dl-x.mkv', 'downloading', 'x.mkv'))
        self.assertIn('message', out)

    def test_check_answers_one_entry_per_job_in_order(self):
        def fake_state(unit, dest=None, name=None):
            return {'unit': unit, 'dest': dest, 'name': name, 'active': True,
                    'state': 'downloading' if name.startswith('a') else 'finished', 'bytes': 10}
        with patch.object(lib, 'job_state', side_effect=fake_state), patch.object(lib, 'private_host', return_value=False):
            out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'check',
                                 'jobs': [{'dest': lib.MOVIES_DIR, 'name': 'a.mkv'}, {'dest': lib.MOVIES_DIR, 'name': 'b.mkv'}]})
        self.assertTrue(out['ok'])
        self.assertEqual([j['name'] for j in out['jobs']], ['a.mkv', 'b.mkv'])
        self.assertEqual([j['state'] for j in out['jobs']], ['downloading', 'finished'])
        self.assertNotIn('active', out['jobs'][0])

    def test_check_refuses_bad_or_oversized_requests(self):
        for jobs in ('not a list', 4, ['x'], [{'dest': '/etc', 'name': 'x.mkv'}],
                     [{'dest': lib.MOVIES_DIR, 'name': '../x.mkv'}], [{}] * 61,
                     [{'dest': lib.MOVIES_DIR, 'name': 'x.mkv'}] * (lib.MAX_CHECK_JOBS + 1)):
            self.assertFalse(lib.rpc_reply({'v': lib.VERSION, 'cmd': 'check', 'jobs': jobs})['ok'], jobs)

    def test_stop_needs_a_name_the_worker_can_find(self):
        # a name in any file type is stoppable; only a name that is not a file name at all is refused
        for name, why in (('gone.mkv', 'no download'), ('notes.txt', 'no download'), ('10Mb.dat', 'no download'),
                         ('../escape.mkv', 'cannot use')):
            with patch.object(lib, 'unit_active', return_value='inactive'):
                out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'stop', 'name': name})
            self.assertFalse(out['ok'], name)
            self.assertIn(why, out['error'], name)

    def test_an_internal_error_is_not_leaked_as_a_traceback(self):
        with patch.object(lib, 'start_debrid', side_effect=ZeroDivisionError('secret /root/path detail')):
            out = lib.rpc_reply({'v': lib.VERSION, 'cmd': 'start', 'url': URL, 'dest': lib.MOVIES_DIR})
        self.assertFalse(out['ok'])
        self.assertNotIn('secret', out['error'])
        self.assertNotIn('Traceback', out['error'])


class StopDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.jobs = os.path.join(root, 'jobs')
        os.makedirs(self.jobs)
        self.patches = [patch.object(lib, 'SHARE_ROOT', root), patch.object(lib, 'JOB_DIR', self.jobs),
                        patch.object(lib, 'MOVIES_DIR', os.path.join(root, 'Movies')),
                        patch.object(lib, 'SHOWS_DIR', os.path.join(root, 'Shows'))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(self.tmp.cleanup)

    def test_stopping_an_active_unit_stops_it_and_records_it(self):
        name, unit = 'The.Show.S01E01.mkv', lib.unit_name('The.Show.S01E01.mkv')
        lib.record_job(unit, 'complete', lib.MOVIES_DIR, name)
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch.object(lib, 'unit_active', return_value='active'), patch.object(lib.subprocess, 'run', side_effect=fake_run):
            out = lib.stop_download(name)
        self.assertEqual(calls, [['systemctl', 'stop', unit]])
        self.assertEqual(out['state'], 'stopped')
        self.assertEqual(lib.read_job(unit)['state'], 'stopped')

    def test_a_unit_that_already_gone_is_still_marked_stopped(self):
        unit = lib.unit_name('Gone.mkv')
        lib.record_job(unit, 'running', lib.MOVIES_DIR, 'Gone.mkv')
        with patch.object(lib, 'unit_active', return_value='inactive'), patch.object(lib.subprocess, 'run') as run:
            out = lib.stop_download('Gone.mkv')
        run.assert_not_called()
        self.assertEqual((out['state'], out['message']), ('stopped', 'marked %s stopped' % unit))

    def test_a_rate_limited_download_of_a_plain_file_can_be_stopped(self):
        # The rate-limited path downloads any file type, so its names are not media names.
        name, unit = '10Mb.dat', lib.unit_name('10Mb.dat')
        calls = []
        with patch.object(lib, 'unit_active', return_value='active'), \
             patch.object(lib.subprocess, 'run', side_effect=lambda argv, **kw: calls.append(argv) or type(
                 'R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()):
            out = lib.stop_download(name)
        self.assertEqual(calls, [['systemctl', 'stop', unit]])
        self.assertEqual(out['state'], 'stopped')

    def test_nothing_to_stop_is_refused(self):
        with patch.object(lib, 'unit_active', return_value='inactive'):
            with self.assertRaises(lib.BadDownload):
                lib.stop_download('never-started.mkv')

    def test_a_bad_name_is_refused_before_any_unit_touches_systemctl(self):
        with patch.object(lib, 'unit_active', return_value='active'), patch.object(lib.subprocess, 'run') as run:
            for bad in ('../escape.mkv', 'a/b.mkv', '', '.', 5):
                with self.assertRaises(lib.BadDownload):
                    lib.stop_download(bad)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
