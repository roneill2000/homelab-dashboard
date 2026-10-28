import http.client
import json
import os
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_PATH = os.path.join(HERE, "syslog-monitor.py")
HOST1_LINE = "2026-10-27T09:58:41Z host1 sshd[1234]: Accepted publickey for admin from 10.0.0.5"


def load_module():
    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    ns = {"__name__": "syslog_monitor", "__file__": SOURCE_PATH}
    exec(compile(source, SOURCE_PATH, "exec"), ns)
    return ns


class SyslogMonitorTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.log_dir = os.path.join(self.base.name, "remote")
        os.makedirs(self.log_dir)
        with open(os.path.join(self.log_dir, "host1.log"), "w", encoding="utf-8") as f:
            f.write(HOST1_LINE + "\n")
        with open(os.path.join(self.base.name, "outside.log"), "w", encoding="utf-8") as f:
            f.write("SECRET-OUTSIDE\n")

        ns = load_module()
        ns["LOG_DIR"] = self.log_dir
        self.server = ns["ThreadedSyslogServer"](("127.0.0.1", 0), ns["SyslogHandler"])
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def host_header(self):
        return "127.0.0.1:%d" % self.port

    def request(self, method, path, host=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host or self.host_header())
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            r = conn.getresponse()
            return r, r.read()
        finally:
            conn.close()

    def test_logs_api_returns_host_lines_with_hardening_headers(self):
        r, body = self.request("GET", "/api/logs?host=host1")
        self.assertEqual(r.status, 200)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        lines = json.loads(body.decode("utf-8"))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["host"], "host1")
        self.assertIn("Accepted publickey", lines[0]["line"])

    def test_log_path_traversal_is_never_served(self):
        for value in ("../outside", "..%2Foutside", "/etc/passwd", "host1/../../outside"):
            r, body = self.request("GET", "/api/logs?host=" + value)
            self.assertEqual(r.status, 200, value)
            self.assertNotIn("SECRET-OUTSIDE", body.decode("utf-8"), value)
            self.assertEqual(json.loads(body.decode("utf-8")), [], value)

    def test_clear_refuses_cross_site_requests(self):
        host = self.host_header()
        r, body = self.request("POST", "/api/clear", host=host)
        self.assertEqual(r.status, 200)
        self.assertTrue(json.loads(body.decode("utf-8"))["ok"])

        r, _ = self.request("POST", "/api/clear", host=host, Origin="http://" + host)
        self.assertEqual(r.status, 200, "same-origin browser request")

        r, _ = self.request("POST", "/api/clear", host="logs.lan", Origin="https://logs.lan")
        self.assertEqual(r.status, 200, "logs.lan proxy")

        r, _ = self.request("POST", "/api/clear", host=host, Origin="https://evil.example")
        self.assertEqual(r.status, 403, "foreign origin")

        r, _ = self.request("POST", "/api/clear", host=host, **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403, "cross-site fetch")

        r, _ = self.request("POST", "/api/clear", host=host, Origin="null")
        self.assertEqual(r.status, 403, "opaque origin")

    def test_page_escapes_host_names(self):
        r, body = self.request("GET", "/")
        self.assertEqual(r.status, 200)
        html = body.decode("utf-8")
        self.assertIn("data-host=", html)
        self.assertIn("escHtml(h.host)", html)
        self.assertNotIn("selectHostPill('${h.host}')", html)
        self.assertNotIn("selectHostPill('${{h.host}}')", html)


if __name__ == "__main__":
    unittest.main()
