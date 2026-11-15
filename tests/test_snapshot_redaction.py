"""Synthetic privacy regressions; no real credentials or log payloads."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nightly.log_snapshot import group_recent, scrub


class SnapshotRedactionTests(unittest.TestCase):
    def test_quoted_secret_fields_keep_json_valid(self):
        raw=json.dumps({'token':'synthetic one', 'apiKey':'synthetic two',
                        'password':'synthetic "quoted" value',
                        'access_token':'synthetic three', 'refresh-token':'synthetic four'})
        cleaned=scrub(raw)
        self.assertNotIn('synthetic',cleaned)
        self.assertTrue(all(v=='[redacted]' for v in json.loads(cleaned).values()))

    def test_authorization_in_json_and_plain_logs(self):
        for raw in ['Authorization: Bearer SYNTHETIC_CREDENTIAL',
                    'authorization=Basic SYNTHETIC_CREDENTIAL',
                    '{"Authorization": "Bearer SYNTHETIC_CREDENTIAL"}',
                    "'authorization': 'Basic SYNTHETIC_CREDENTIAL'"]:
            with self.subTest(raw=raw):
                self.assertNotIn('SYNTHETIC_CREDENTIAL',scrub(raw))

    def test_url_userinfo_is_removed_without_removing_failure_facts(self):
        for url in ['https://user:SYNTHETIC_CREDENTIAL@example.invalid/path',
                    'https://SYNTHETIC_CREDENTIAL@example.invalid/path',
                    'rtsp://user:SYNTHETIC_CREDENTIAL@example.invalid/path']:
            cleaned=scrub('FAILURE status=1 '+url)
            self.assertNotIn('SYNTHETIC_CREDENTIAL',cleaned)
            self.assertIn('status=1',cleaned)
            self.assertIn('example.invalid/path',cleaned)

    def test_single_quotes_spaces_and_unquoted_assignments(self):
        cleaned=scrub("FAILURE password='SYNTHETIC one' token=SYNTHETIC_TWO; status=1/FAILURE")
        self.assertNotIn('SYNTHETIC',cleaned)
        self.assertIn('status=1/FAILURE',cleaned)

    def test_nonsecret_operational_fields_are_preserved(self):
        raw='filebrowser.service: Main process exited, code=exited, status=1/FAILURE'
        self.assertEqual(scrub(raw),raw)


CRIT=[re.compile(r'(?i)\bI/O error')]
WARN=[re.compile(r'(?i)link is down')]
TS0=int(datetime(2026,10,27,10,0,tzinfo=timezone.utc).timestamp())  # 1793066400


class GroupRecentTests(unittest.TestCase):
    """Item 4: the live re-check behind /api/group-recent answers from the tail of each file only."""

    def write(self, d, name, lines):
        Path(d, name + '.log').write_text('\n'.join(lines) + '\n')

    def line(self, host, minute, text, pid=None):
        ts = datetime(2026, 10, 27, 10, minute, tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
        return f'{ts} {host} app{pid or ""}: {text}'

    def gid(self, host, line, level='WARNING'):
        from nightly.log_snapshot import normalize
        import hashlib
        message = re.sub(r'^\S+\s+', '', scrub(line))
        while message.casefold().startswith(host + ' '):
            message = message[len(host) + 1:]
        message = re.sub(r'\b([\w.-]+)\[\d+\]:', r'\1:', message)
        return hashlib.sha256(repr((host, level, normalize(message))).encode()).hexdigest()[:12]

    def test_counts_only_lines_at_or_after_since_for_that_group(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, 'unraid', [self.line('unraid', 0, 'link is down on br0', '[11]'),
                                     self.line('unraid', 20, 'link is down on br0', '[12]')])
            self.write(d, 'orangepi', [self.line('orangepi', 30, 'link is down on wlan0')])
            gid = self.gid('unraid', self.line('unraid', 0, 'link is down on br0', '[11]'))
            since = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc).timestamp()
            out = group_recent(Path(d), gid, since, CRIT, WARN)
            self.assertEqual(out['count'], 1, 'only the 10:20 line is at or after 10:10')
            self.assertEqual(out['files_scanned'], 2)
            self.assertIs(out['error'], False)
            self.assertIn('link is down', out['last_line']['line'])
            out = group_recent(Path(d), gid, datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc).timestamp(), CRIT, WARN)
            self.assertEqual(out['count'], 2, 'with an older since both unraid lines count, the orangepi one is another group')

    def test_read_is_bounded_to_the_tail(self):
        with tempfile.TemporaryDirectory() as d:
            gid = self.gid('unraid', self.line('unraid', 0, 'link is down on br0'))
            filler = [self.line('unraid', 0, 'link is down on br0')]
            filler += [f'2026-10-27T10:00:{m:02d}Z unraid filler: {"x" * 60} {m}' for m in range(60)]
            filler += [self.line('unraid', 10, 'link is down on br0')]
            self.write(d, 'unraid', filler)
            size = Path(d, 'unraid.log').stat().st_size
            self.assertGreater(size, 4096)
            since = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc).timestamp()
            # The 10:00 matching line sits far outside a 4 KiB tail; only the last one is inside it.
            out = group_recent(Path(d), gid, since, CRIT, WARN, tail_bytes=4096)
            self.assertEqual(out['count'], 1, 'a line older than the bounded tail is not counted')
            out = group_recent(Path(d), gid, since, CRIT, WARN, tail_bytes=size)
            self.assertEqual(out['count'], 2)

    def test_redaction_applies_to_everything_returned(self):
        with tempfile.TemporaryDirectory() as d:
            line = self.line('unraid', 5, 'link is down on br0; password=SUPER_SYNTHETIC')
            self.write(d, 'unraid', [line])
            gid = self.gid('unraid', line)
            out = group_recent(Path(d), gid, TS0 - 100, CRIT, WARN)
            self.assertEqual(out['count'], 1)
            self.assertNotIn('SUPER_SYNTHETIC', out['last_line']['line'])
            self.assertNotIn('SUPER_SYNTHETIC', json.dumps(out))

    def test_unreadable_file_is_reported_not_silently_zero(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, 'unraid', [self.line('unraid', 0, 'link is down on br0')])
            out = group_recent(Path(d), 'deadbeef', TS0 - 100, CRIT, WARN)
            self.assertEqual(out['count'], 0)
            self.assertIs(out['error'], False)
            Path(d, 'unraid.log').unlink()
            Path(d, 'unraid.log').mkdir()  # unreadable for any user: open() on a directory is an OSError
            out = group_recent(Path(d), 'deadbeef', TS0 - 100, CRIT, WARN)
            self.assertIs(out['error'], True, 'a file that cannot be read is an error, not a quiet zero')


if __name__=='__main__':
    unittest.main()
