import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_PATH = os.path.join(HERE, "syslog-monitor.py")
HOST1_LINE = "2026-10-27T09:58:41Z host1 sshd[1234]: Accepted publickey for admin from 10.0.0.5"

# The monitor imports homelab_log_snapshot (installed by nightly/install_snapshot.py on Proxmox) at request
# time; syslog/homelab_log_snapshot.py is the test shim over the repo's nightly/log_snapshot.py.
if HERE not in sys.path:
    sys.path.insert(0, HERE)


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


class GroupRecentEndpointTests(unittest.TestCase):
    """Item 4: /api/group-recent answers 'is this group's line still being logged?'."""

    LINES = [
        "2026-10-27T10:00:00Z host1 filebrowser[1234]: Failed to start filebrowser.service from 10.0.0.5 port 2222",
        "2026-10-27T11:30:00Z host1 filebrowser[5678]: Failed to start filebrowser.service from 10.0.0.9 port 3333",
        "2026-10-27T11:35:00Z host1 filebrowser[999]: Failed to start filebrowser.service from 192.168.0.9 port 3333",
        "2026-10-27T09:00:00Z host2 kernel: blk_update_request: I/O error, dev sda, sector 12345",
    ]

    def setUp(self):
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.log_dir = os.path.join(self.base.name, "remote")
        os.makedirs(self.log_dir)
        with open(os.path.join(self.log_dir, "host1.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(self.LINES[:3]) + "\n")
        with open(os.path.join(self.log_dir, "host2.log"), "w", encoding="utf-8") as f:
            f.write(self.LINES[3] + "\n")

        ns = load_module()
        ns["LOG_DIR"] = self.log_dir
        self.patterns = (ns["CRITICAL_PATTERNS"], ns["WARNING_PATTERNS"])
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

    def group_id(self, fname, line):
        """The id the nightly snapshot would give this line's group, computed the same way."""
        from nightly import log_snapshot as s
        stem = os.path.basename(fname)[: -len(".log")]
        level = "CRITICAL" if any(r.search(line) for r in self.patterns[0]) else "WARNING"
        self.assertTrue(any(r.search(line) for r in self.patterns[0] + self.patterns[1]),
                        "fixture line must be one the monitor flags: " + line)
        message = re.sub(r"^\S+\s+", "", s.scrub(line))
        while message.casefold().startswith(stem + " "):
            message = message[len(stem) + 1:]
        message = re.sub(r"\b([\w.-]+)\[\d+\]:", r"\1:", message)
        return hashlib.sha256(repr((stem, level, s.normalize(message))).encode()).hexdigest()[:12]

    def test_group_recent_counts_matching_lines_since(self):
        gid = self.group_id("host1.log", self.LINES[0])
        r, body = self.request("GET", "/api/group-recent?id=" + gid + "&since=1793095200")  # 2026-10-27T09:53:20Z
        self.assertEqual(r.status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["count"], 2, "both normalized copies of the line count")
        self.assertEqual(data["files_scanned"], 2)
        self.assertIs(data["error"], False)
        self.assertIn("Failed to start filebrowser.service", data["last_line"]["line"])

    def test_group_recent_respects_since_and_other_groups(self):
        gid = self.group_id("host1.log", self.LINES[0])       # 10:00Z and 11:30Z, both <ip>
        lan = self.group_id("host1.log", self.LINES[2])       # 11:35Z, <lan:.9> is its own group
        other = self.group_id("host2.log", self.LINES[3])     # 09:00Z CRITICAL on another host
        # Lines older than `since` are not "recent": since is the snapshot time.
        r, body = self.request("GET", "/api/group-recent?id=" + gid + "&since=1793101200")  # 2026-10-27T11:40:00Z
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 0, "lines older than since do not count")
        # A LAN address normalizes to <lan:.9>, so it is a different group from <ip>.
        self.assertNotEqual(lan, gid, "<lan:.9> is not <ip>")
        r, body = self.request("GET", "/api/group-recent?id=" + lan + "&since=1793095200")  # 10:00Z
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 1, "the LAN line is its own group")
        # The other host's line is not this group's, even though since covers it.
        r, body = self.request("GET", "/api/group-recent?id=" + gid + "&since=1793088000")  # 08:00Z
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 2, "only host1's two matching lines")
        # And this group's line does count once its own timestamp is inside the window.
        r, body = self.request("GET", "/api/group-recent?id=" + other + "&since=1793088000")
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 1, "host2's line is its own group")
        r, body = self.request("GET", "/api/group-recent?id=" + other + "&since=1793095200")  # 10:00Z > 09:00Z
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 0, "its own line predates since")

    def test_group_recent_rejects_missing_parameters(self):
        r, _ = self.request("GET", "/api/group-recent")
        self.assertEqual(r.status, 400)
        r, _ = self.request("GET", "/api/group-recent?id=abc123")
        self.assertEqual(r.status, 400)
        r, _ = self.request("GET", "/api/group-recent?id=abc123&since=not-a-time")
        self.assertEqual(r.status, 400)
        r, _ = self.request("GET", "/api/group-recent?id=&since=1793095200")
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
