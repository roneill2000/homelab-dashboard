"""Synthetic privacy regressions; no real credentials or log payloads."""
import json
import unittest

from nightly.log_snapshot import scrub


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


if __name__=='__main__':
    unittest.main()
