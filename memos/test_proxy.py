import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch,MagicMock

with patch.object(Path,'read_text',return_value='{}'):
    spec=importlib.util.spec_from_file_location('proxy',Path(__file__).with_name('lan_proxy.py'))
    proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)

class ProxyTests(unittest.TestCase):
    def request(self,after_headers):
        h=object.__new__(proxy.Handler)
        h.headers={'Host':'192.168.0.18:5230'};h.path='/api/v1/memos';h.command='GET'
        h.requestline='GET /api/v1/memos HTTP/1.1';h.request_version='HTTP/1.1';h.wfile=io.BytesIO();h.rfile=io.BytesIO()
        conn=MagicMock();response=conn.getresponse.return_value
        response.status=200;response.getheaders.return_value=[('Content-Type','application/json')]
        if after_headers:response.read.side_effect=OSError('interrupted upstream')
        else:conn.getresponse.side_effect=OSError('upstream unavailable')
        with patch.object(proxy,'token',return_value={'accessToken':'fake'}),patch.object(proxy.http.client,'HTTPConnection',return_value=conn):
            h.handle_request()
        conn.close.assert_called_once()
        return h.wfile.getvalue()
    def test_failure_before_headers_returns_502(self):
        result=self.request(False)
        self.assertTrue(result.startswith(b'HTTP/1.1 502'))
        self.assertEqual(result.count(b'HTTP/1.1'),1)
    def test_failure_after_headers_never_appends_second_response(self):
        result=self.request(True)
        self.assertTrue(result.startswith(b'HTTP/1.1 200'))
        self.assertEqual(result.count(b'HTTP/1.1'),1)
        self.assertNotIn(b'502',result)
