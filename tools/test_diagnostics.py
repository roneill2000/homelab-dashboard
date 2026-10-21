import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class DiagnosticsTests(unittest.TestCase):
    def run_warning(self,contents):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'syslog'
            if contents is not None:path.write_text(contents)
            script=Path(__file__).with_name('diagnostics.sh').read_text().replace('/var/log/syslog',str(path))
            return subprocess.run(['/bin/bash','-s','unraid'],input=script,text=True,capture_output=True,
                                  env={**os.environ,'SSH_ORIGINAL_COMMAND':'warnings'})
    def test_empty_matches_are_successful(self):
        result=self.run_warning('normal successful startup\n')
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,'')
    def test_missing_source_is_failure(self):
        result=self.run_warning(None)
        self.assertNotEqual(result.returncode,0);self.assertTrue(result.stderr)
    def test_repeats_collapsed_and_bounded(self):
        lines=''.join(f'Oct 20 13:0{i%10}:01 Tower kernel[{i}]: ERROR disk {i%3}\n' for i in range(90))
        lines+=''.join(f'Oct 20 14:00:00 Tower app: failed job {i}\n' for i in range(40))
        result=self.run_warning(lines)
        self.assertEqual(result.returncode,0)
        out=result.stdout.splitlines()
        self.assertEqual(len(out),25)
        self.assertEqual(sorted(l.split(None,1) for l in out[:3]),[['30','kernel: ERROR disk 0'],['30','kernel: ERROR disk 1'],['30','kernel: ERROR disk 2']])
