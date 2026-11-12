import ast
import threading
import unittest
from unittest.mock import patch

from backend_patch import patch_widget_more, patch_widget_strata, patch_widget_dns, patch_widget_cloud, patch_backend, patch_compression, patch_fonts, patch_hardening, patch_widget, patch_ai_live, patch_inline_safe, patch_jellyfin_auth, patch_speedtest_latest_completed, patch_speedtest_time

FIXTURE = '''
import time
TELEMETRY = {'pve': {}, 'unraid': {}, 'scrutiny': {}, 'media': {'radarr': {'online': True, 'movies': 99}}, 'pihole': {'primary': {}, 'secondary': {}}, 'services': {}}
SECRET = 'fixture-do-not-log-123'
def poll_proxmox():
    try:
        cpu_temp = 62
        nvme_temp = 39
        scratch_pct = 3.0
        try:
            sensors = fetch_sensors()
            for s in sensors:
                cpu_temp = round(float(s.get('value', 62)))
        except Exception:
            pass
        TELEMETRY['pve'] = {'online': True, 'cpu_temp': cpu_temp, 'nvme_temp': nvme_temp, 'scratch_pct': scratch_pct}
    except Exception:
        TELEMETRY['pve']['online'] = False

def poll_unraid_and_scrutiny():
    dev = {}
    TELEMETRY['scrutiny'] = {'online': True, 'drives': [{'healthy': dev.get('device_status', 0) == 0}]}

def poll_dns_and_speed():
    if dns_primary:
        TELEMETRY['pihole']['primary'] = {'online': True, 'blocking': dns_primary['blocking']}
        TELEMETRY['pihole']['blocking'] = dns_primary['blocking']
    else:
        TELEMETRY['pihole']['primary']['online'] = False
    if dns_secondary:
        TELEMETRY['pihole']['secondary'] = {'online': True, 'blocking': dns_secondary['blocking']}
    else:
        TELEMETRY['pihole']['secondary']['online'] = False

def poll_media_and_syslog():
    if media_ok:
        TELEMETRY['media']['radarr'] = {'online': True, 'movies': 3, 'queue': 0}
    else:
        TELEMETRY['media']['radarr']['online'] = False

def poll_network_and_matrix():
    TELEMETRY['services'] = {'router': True}

def action_handler():
    return SECRET
'''


class BackendPatchTests(unittest.TestCase):
    def context(self):
        source = patch_backend(FIXTURE)
        namespace = {'STATE_LOCK': threading.Lock(), 'fetch_sensors': lambda: [], 'media_ok': True}
        exec(compile(source, '<fixture>', 'exec'), namespace)
        return source, namespace

    def test_idempotence_and_unrelated_actions(self):
        source, ctx = self.context()
        self.assertEqual(patch_backend(source), source)
        self.assertTrue(patch_backend('#!/usr/bin/python3\n' + FIXTURE).startswith('#!/usr/bin/python3\n'))
        self.assertEqual(ctx['action_handler'](), 'fixture-do-not-log-123')
        original = ast.parse(FIXTURE)
        updated = ast.parse(source)
        action = lambda t: next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == 'action_handler')
        self.assertEqual(ast.dump(action(original)), ast.dump(action(updated)))

    def test_absent_sensors_and_missing_health_are_unknown(self):
        _, ctx = self.context()
        ctx['poll_proxmox']()
        pve = ctx['TELEMETRY']['pve']
        self.assertIsNone(pve['cpu_temp'])
        self.assertIsNone(pve['nvme_temp'])
        self.assertIsNone(pve['scratch_pct'])
        ctx['fetch_sensors'] = lambda: [{'value': None}]
        ctx['poll_proxmox']()
        self.assertIsNone(ctx['TELEMETRY']['pve']['cpu_temp'])
        ctx['fetch_sensors'] = lambda: [{'value': '73.2'}]
        ctx['poll_proxmox']()
        self.assertEqual(ctx['TELEMETRY']['pve']['cpu_temp'], 73)
        ctx['poll_unraid_and_scrutiny']()
        self.assertIsNone(ctx['TELEMETRY']['scrutiny']['drives'][0]['healthy'])

    def test_failure_preserves_last_success_and_values(self):
        _, ctx = self.context()
        with patch('time.time', return_value=100):
            ctx['poll_media_and_syslog']()
        self.assertNotIn('radarr', ctx['TELEMETRY']['media'])
        first = ctx['TELEMETRY']['media']['readarr']
        self.assertEqual(first['books'], 3)
        self.assertEqual(first['last_success_at'], 100)
        ctx['media_ok'] = False
        with patch('time.time', return_value=200):
            ctx['poll_media_and_syslog']()
        latest = ctx['TELEMETRY']['media']['readarr']
        self.assertEqual(latest['books'], 3)
        self.assertEqual(latest['last_success_at'], 100)
        self.assertEqual(latest['last_attempt_at'], 200)
        self.assertFalse(latest['online'])
        self.assertEqual(latest['error'], 'Source poll failed')

    def test_dns_requires_consistent_available_nodes(self):
        _, ctx = self.context()
        ctx.update(dns_primary={'blocking': True}, dns_secondary={'blocking': False})
        ctx['poll_dns_and_speed']()
        dns = ctx['TELEMETRY']['pihole']
        self.assertIsNone(dns['blocking'])
        self.assertEqual(dns['status'], 'mixed')
        ctx['dns_secondary'] = {'blocking': True}
        with patch('time.time', return_value=100):
            ctx['poll_dns_and_speed']()
        self.assertTrue(ctx['TELEMETRY']['pihole']['blocking'])
        ctx['dns_secondary'] = None
        with patch('time.time', return_value=200):
            ctx['poll_dns_and_speed']()
        dns = ctx['TELEMETRY']['pihole']
        self.assertIsNone(dns['blocking'])
        self.assertFalse(dns['online'])
        self.assertEqual(dns['last_success_at'], 100)
        self.assertEqual(dns['secondary']['last_success_at'], 100)
        self.assertEqual(dns['status'], 'unknown')

    def test_source_shape_drift_fails_before_mutation(self):
        with self.assertRaises(ValueError):
            patch_backend('def poll_proxmox(): pass\n')


class JellyfinAuthTests(unittest.TestCase):
    def test_header_rewritten_once_and_idempotent(self):
        src = "def task_jellyfin():\n    jf_hdr = {'X-Emby-Token': '" + 'a' * 32 + "'}\n"
        out = patch_jellyfin_auth(src)
        self.assertIn("{'Authorization': 'MediaBrowser Token=\"" + 'a' * 32 + "\"'}", out)
        self.assertNotIn('X-Emby-Token', out)
        self.assertEqual(patch_jellyfin_auth(out), out)
        with self.assertRaises(ValueError):
            patch_jellyfin_auth('def task_jellyfin(): pass\n')


class SpeedtestTests(unittest.TestCase):
    SRC = ("def poll():\n    try:\n        st = fetch_json('http://192.168.0.14/api/v1/results/latest', headers={'Authorization': 'Bearer x', 'Accept': 'application/json'}).get('data', {})\n"
           "        return st.get('download_bits')\n    except Exception:\n        return 'unavailable'\n")

    def run_with(self, results):
        ns = {'fetch_json': lambda url, headers=None: {'data': results}}
        exec(compile(patch_speedtest_latest_completed(self.SRC), 't', 'exec'), ns)
        return ns['poll']()

    def test_skips_failed_and_never_invents_zero(self):
        self.assertEqual(self.run_with([{'status': 'failed'}, {'status': 'completed', 'download_bits': 2343}]), 2343)
        self.assertEqual(self.run_with([{'status': 'failed'}]), 'unavailable')
        out = patch_speedtest_latest_completed(self.SRC)
        self.assertEqual(patch_speedtest_latest_completed(out), out)


class SpeedtestTimeTests(unittest.TestCase):
    def test_tested_at_added_once(self):
        src = "x = {'server': st.get('data', {}).get('server', {}).get('name', 'Comcast')}\n"
        out = patch_speedtest_time(src)
        self.assertIn("'tested_at': st.get('created_at')", out)
        self.assertEqual(patch_speedtest_time(out), out)


class CompressionTests(unittest.TestCase):
    SRC = ('from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n'
           'PAGE = "<html>" + "x" * 5000 + "</html>"\n'
           'BUNDLE = b"var react = 1;" * 500\n'
           'class DashboardHandler(BaseHTTPRequestHandler):\n'
           '    def log_message(self, format, *args):\n'
           '        pass\n'
           '    def do_GET(self):\n'
           '        if self.path == "/static/react.js":\n'
           '            if True:\n'
           '                body = BUNDLE\n'
           '                self.send_response(200)\n'
           '                self.send_header("Content-Type", "application/javascript; charset=utf-8")\n'
           '                self.send_header("Cache-Control", "public, max-age=86400")\n'
           '                self.end_headers()\n'
           '                self.wfile.write(body)\n'
           '        else:\n'
           '            body = PAGE.encode("utf-8")\n'
           '            self.send_response(200)\n'
           '            self.send_header("Content-Type", "text/html; charset=utf-8")\n'
           '            self.send_header("Cache-Control", "no-cache")\n'
           '            self.end_headers()\n'
           '            self.wfile.write(body)\n')

    def serve(self):
        import http.client
        ns = {}
        exec(compile(patch_compression(self.SRC), 'compression', 'exec'), ns)
        server = ns['ThreadingHTTPServer'](('127.0.0.1', 0), ns['DashboardHandler'])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def get(path, **headers):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1])
            conn.request('GET', path, headers=headers)
            r = conn.getresponse()
            return r, r.read()
        return get

    def test_page_gzipped_only_when_accepted_and_never_cached(self):
        import gzip
        get = self.serve()
        r, body = get('/', **{'Accept-Encoding': 'gzip, deflate, br'})
        self.assertEqual(r.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(r.getheader('Cache-Control'), 'no-cache')
        self.assertIsNone(r.getheader('ETag'))
        self.assertEqual(gzip.decompress(body).decode(), '<html>' + 'x' * 5000 + '</html>')
        r, body = get('/')
        self.assertIsNone(r.getheader('Content-Encoding'))
        self.assertTrue(body.startswith(b'<html>'))

    def test_bundle_revalidates_with_304(self):
        get = self.serve()
        r, _ = get('/static/react.js', **{'Accept-Encoding': 'gzip'})
        etag = r.getheader('ETag')
        self.assertTrue(etag.endswith('-gz"'))
        r, body = get('/static/react.js', **{'Accept-Encoding': 'gzip', 'If-None-Match': etag})
        self.assertEqual((r.status, body), (304, b''))
        r, body = get('/static/react.js', **{'If-None-Match': etag})
        self.assertEqual(r.status, 304)
        r, body = get('/static/react.js', **{'If-None-Match': '"stale"'})
        self.assertEqual((r.status, body[:13]), (200, b'var react = 1'))

    def test_idempotent_and_refuses_drifted_source(self):
        out = patch_compression(self.SRC)
        self.assertEqual(patch_compression(out), out)
        with self.assertRaises(ValueError):
            patch_compression('class DashboardHandler(BaseHTTPRequestHandler):\n    pass\n')


class InlineSafeTests(unittest.TestCase):
    PAGE = ('<!doctype html><html><head><title>x</title></head><body><script src="/static/react.js"></script>'
            '<script>/*__INITIAL_TELEMETRY_SLOT__*/</script><script>app()</script></body></html>')
    SRC = ('import json\n'
           'def render(INDEX_HTML, TELEMETRY):\n'
           '    if True:\n'
           '        if True:\n'
           '            initial_json = json.dumps(TELEMETRY)\n'
           '            rendered = INDEX_HTML.replace(\n'
           '                "/*__INITIAL_TELEMETRY_SLOT__*/",\n'
           '                f"window.__INITIAL_TELEMETRY__ = {initial_json};"\n'
           '            )\n'
           '            return rendered\n')

    def render(self, telemetry):
        ns = {}
        exec(compile(patch_inline_safe(self.SRC), 'inline', 'exec'), ns)
        return ns['render'](self.PAGE, telemetry)

    def test_hostile_log_line_cannot_close_the_script(self):
        import json, re
        msg = 'segfault at 0 </script><script>steal()</script> & \u2028 <!--'
        out = self.render({'syslog': {'latest_event': {'msg': msg}}})
        self.assertNotIn('steal()</script>', out)
        self.assertEqual(out.lower().count('</script'), 3)
        data = re.search(r'window.__INITIAL_TELEMETRY__ = (.*?);</script>', out).group(1)
        self.assertEqual(json.loads(data)['syslog']['latest_event']['msg'], msg)

    def test_every_page_script_gets_this_response_nonce_and_csp(self):
        import re
        out = self.render({})
        nonce = re.search(r"script-src 'nonce-([^']+)'", out).group(1)
        self.assertEqual(len(re.findall('<script nonce="' + re.escape(nonce) + '"', out)), 3)
        self.assertNotIn('<script>', out)
        self.assertLess(out.index('Content-Security-Policy'), out.index('<script'))
        self.assertNotEqual(nonce, re.search(r"nonce-([^']+)'", self.render({})).group(1))

    def test_idempotent_and_refuses_drifted_source(self):
        out = patch_inline_safe(self.SRC)
        self.assertEqual(patch_inline_safe(out), out)
        with self.assertRaises(ValueError):
            patch_inline_safe('def render(): pass\n')


class HardeningTests(unittest.TestCase):
    SRC = CompressionTests.SRC + ('    def do_POST(self):\n'
                                  '        self.send_response(204)\n'
                                  '        self.end_headers()\n')

    def serve(self):
        import http.client
        ns = {}
        exec(compile(patch_hardening(patch_compression(self.SRC)), 'hardening', 'exec'), ns)
        self.assertEqual(ns['DashboardHandler'].timeout, 60)
        server = ns['ThreadingHTTPServer'](('127.0.0.1', 0), ns['DashboardHandler'])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def req(method, path, host):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1])
            conn.putrequest(method, path, skip_host=True)
            conn.putheader('Host', host)
            conn.endheaders()
            r = conn.getresponse()
            return r, r.read()
        return req

    def test_page_cannot_be_framed(self):
        req = self.serve()
        r, _ = req('GET', '/', '192.168.0.2')
        self.assertEqual(r.getheader('X-Frame-Options'), 'DENY')
        self.assertEqual(r.getheader('Content-Security-Policy'), "frame-ancestors 'none'")

    def test_rebinding_hosts_refused_lan_names_allowed(self):
        req = self.serve()
        for host in ('192.168.0.2', '192.168.0.2:80', 'home.lan', 'homepage', 'localhost:8799', '127.0.0.1'):
            self.assertEqual(req('GET', '/', host)[0].status, 200, host)
        for host in ('evil.example', 'attacker.com:80', '8.8.8.8', 'home.lan.evil.example'):
            self.assertEqual(req('GET', '/', host)[0].status, 421, host)
            self.assertEqual(req('POST', '/api/action/x', host)[0].status, 421, host)

    def test_idempotent_and_refuses_drifted_source(self):
        once = patch_hardening(patch_compression(self.SRC))
        self.assertEqual(patch_hardening(once), once)
        with self.assertRaises(ValueError):
            patch_hardening('class DashboardHandler(BaseHTTPRequestHandler):\n    pass\n')


class FontsTests(unittest.TestCase):
    SRC = ('from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n'
           'class DashboardHandler(BaseHTTPRequestHandler):\n'
           '    def log_message(self, format, *args):\n'
           '        pass\n'
           '    def _dashboard_send(self, body, ctype, cache, etag=None):\n'
           '        self.send_response(200)\n'
           '        self.send_header("Content-Type", ctype)\n'
           '        self.send_header("Cache-Control", cache)\n'
           '        self.end_headers()\n'
           '        self.wfile.write(body)\n'
           '    def do_GET(self):\n'
           '        if self.path == "/x":\n'
           '            pass\n'
           '        elif self.path in ("/static/react.js", "/static/react-dom.js"):\n'
           '            self.send_error(404)\n'
           '        else:\n'
           '            self.send_error(404)\n')

    def serve(self, fonts_dir):
        import http.client, os
        os.environ['HOMELAB_FONTS_DIR'] = fonts_dir
        self.addCleanup(os.environ.pop, 'HOMELAB_FONTS_DIR', None)
        ns = {'_dashboard_etag': lambda b: '"e"'}
        exec(compile(patch_fonts(self.SRC), 'fonts', 'exec'), ns)
        server = ns['ThreadingHTTPServer'](('127.0.0.1', 0), ns['DashboardHandler'])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def get(path):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1])
            conn.request('GET', path)
            r = conn.getresponse()
            return r, r.read()
        return get

    def test_serves_allowlisted_woff2_and_refuses_everything_else(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            fonts = os.path.join(root, 'fonts'); os.mkdir(fonts)
            open(os.path.join(fonts, 'roboto-flex-latin-abc123.woff2'), 'wb').write(b'wOF2-font')
            open(os.path.join(root, 'secret.woff2'), 'wb').write(b'SECRET')
            get = self.serve(fonts)
            r, body = get('/static/fonts/roboto-flex-latin-abc123.woff2')
            self.assertEqual((r.status, body, r.getheader('Content-Type')), (200, b'wOF2-font', 'font/woff2'))
            self.assertIn('immutable', r.getheader('Cache-Control'))
            for bad in ('/static/fonts/../secret.woff2', '/static/fonts/..%2Fsecret.woff2', '/static/fonts/Roboto.woff2',
                        '/static/fonts/x.ttf', '/static/fonts/missing.woff2'):
                r, body = get(bad)
                self.assertEqual(r.status, 404, bad)
                self.assertNotIn(b'SECRET', body)

    def test_idempotent_and_refuses_drifted_source(self):
        out = patch_fonts(self.SRC)
        self.assertEqual(patch_fonts(out), out)
        with self.assertRaises(ValueError):
            patch_fonts('class DashboardHandler: pass\n')


class WidgetTests(unittest.TestCase):
    SRC = ('import json, threading\n'
           'STATE_LOCK = threading.Lock()\n'
           'TELEMETRY = {}\n'
           'class DashboardHandler(BaseHTTPRequestHandler):\n'
           '    def do_GET(self):\n'
           '        if self.path.startswith("/api/telemetry"):\n'
           '            pass\n')

    def summary(self, telemetry, ids, desktop=None, gpu=None, backup='OK', now=1000, strata=None, updates=None):
        import json, sys, types
        from unittest.mock import mock_open
        ai = types.ModuleType('ai_monitor')
        ai.snapshot = lambda: {'desktop': desktop or {'online': False}, 'strata': strata or {'online': False}}
        ai._pc_gpu = {'latest': gpu}
        ai.pc_gpu_poll = lambda t: None
        sm = types.ModuleType('storage_monitor')
        sm.snapshot = lambda w: {'activity': {'recovery': {'backup': {'latest': {'status': backup}}}}}
        ns = {'BaseHTTPRequestHandler': object}
        exec(compile(patch_widget_more(patch_widget_strata(patch_widget_dns(patch_widget(self.SRC)))), 'widget', 'exec'), ns)
        ns['TELEMETRY'].update(telemetry)
        up = types.ModuleType('update_proxy'); up.status = (lambda: updates) if updates is not None else (lambda: (_ for _ in ()).throw(RuntimeError('ssh')))
        with patch.dict(sys.modules, {'ai_monitor': ai, 'storage_monitor': sm, 'update_proxy': up}), patch('builtins.open', mock_open(read_data=json.dumps(ids))):
            return ns['_dashboard_widget_summary'](now=now)

    def base(self, **over):
        t = {'updated_at': 990, 'services': {'router': True, 'kuma': True, 'last_success_at': 990},
             'pve': {'online': True, 'last_success_at': 990}, 'pihole': {'status': 'protected'},
             'media': {'sabnzbd': {'online': True, 'speed': '0.00 KB/s', 'queue': 0, 'last_success_at': 990}},
             'scrutiny': {'online': True, 'drives': [{'healthy': True}], 'last_success_at': 990},
             'syslog': {'online': True, 'errors_24h': 0, 'last_success_at': 990}}
        t.update(over)
        return t

    def test_quiet_day_pc_off_downloads_idle_no_problems(self):
        s = self.summary(self.base(), ['router', 'kuma', 'pve', 'files'])
        self.assertEqual((s['pc']['state'], s['headline'], s['downloads']['state'], s['problems'], s['status']),
                         ('off', 'PC asleep or off', 'idle', [], 'all_up'))
        self.assertEqual((s['services_up'], s['services_known']), (3, 3))

    def test_model_loaded_downloads_active_and_every_problem_source(self):
        t = self.base(media={'sabnzbd': {'online': True, 'speed': '42.1 MB/s', 'queue': 3, 'last_success_at': 990}},
                      scrutiny={'online': True, 'drives': [{'healthy': False}], 'last_success_at': 990},
                      syslog={'online': True, 'errors_24h': 2, 'last_success_at': 990}, pihole={'status': 'paused'})
        t['services'] = {**t['services'], 'kuma': False}
        desk = {'online': True, 'last_success_at': 995, 'models': [{'id': 'qwen', 'state': 'loaded'}], 'is_active': False}
        gpu = {'power_w': 164.2, 'temperature_c': 62, 'utilization_pct': 97, 'vram_used_mib': 15000, 'vram_total_mib': 16303, 'observed_at': 990}
        s = self.summary(t, ['router', 'kuma'], desktop=desk, gpu=gpu, backup='ERROR')
        self.assertEqual((s['pc']['state'], s['pc']['model'], s['pc']['gpu']['power_w']), ('model_loaded', 'qwen', 164.2))
        self.assertEqual((s['downloads']['state'], s['downloads']['speed'], s['downloads']['queue']), ('active', '42.1 MB/s', 3))
        self.assertEqual(s['problems'], ['1 service down', 'DNS blocking paused', 'Drive health warning', '2 log errors', 'Backup job failed'])
        self.assertEqual(s['status'], 'attention')

    def test_dns_state_for_the_quick_settings_tile(self):
        on = self.summary(self.base(pihole={'status': 'protected', 'blocking': True, 'timer': None}), ['router'])
        self.assertEqual(on['dns'], {'status': 'protected', 'blocking': True, 'seconds_left': None})
        paused = self.summary(self.base(pihole={'status': 'paused', 'blocking': False, 'timer': 241.6}), ['router'])
        self.assertEqual(paused['dns'], {'status': 'paused', 'blocking': False, 'seconds_left': 241})
        stale = self.summary(self.base(updated_at=900, pihole={'status': 'paused', 'blocking': False, 'timer': 60}), ['router'])
        self.assertEqual(stale['dns'], {'status': 'unknown', 'blocking': None, 'seconds_left': None})  # old data never shows as On
        src = patch_widget_dns(patch_widget(self.SRC))
        self.assertEqual(patch_widget_dns(src), src)  # idempotent

    def test_stale_readings_are_a_problem_never_zeros(self):
        s = self.summary(self.base(updated_at=900), ['router', 'pve'])
        self.assertEqual(s['problems'][0], 'No fresh readings')
        self.assertIsNone(s['services_up'])
        s = self.summary(self.base(media={'sabnzbd': {'online': True, 'queue': 0, 'last_success_at': 900}}), ['router'])
        self.assertEqual((s['downloads']['state'], s['downloads']['queue']), ('unknown', None))
        old_gpu = {'power_w': 100, 'observed_at': 800}
        s = self.summary(self.base(), ['router'], desktop={'online': True, 'last_success_at': 995, 'models': []}, gpu=old_gpu)
        self.assertEqual((s['pc']['state'], s['pc']['gpu']), ('idle', None))


    def test_status_first_blocks_storage_network_media_updates(self):
        t = self.base(unraid={'online': True, 'array_pct': 69.0, 'array_free_tb': 1.24, 'cache_pct': 22.1, 'last_success_at': 990},
                      speedtest={'online': True, 'dl': 2342.7, 'ul': 297.9, 'ping': 10.7, 'last_success_at': 990},
                      pihole={'status': 'protected', 'blocking': True, 'pct': 4.4, 'online': True, 'last_success_at': 990})
        t['media'] = {**t['media'], 'jellyfin': {'online': True, 'last_success_at': 990, 'now_playing': [
            {'title': 'Tomorrow', 'series': 'The Bear', 'season': 3, 'episode': 2, 'user': 'ryan', 'paused': False, 'progress': 0.4}]}}
        upd = {'items': [{'state': 'pending'}, {'state': 'current'}, {'state': 'pending'}], 'checked_at': 900}
        s = self.summary(t, ['router'], updates=upd)
        self.assertEqual(s['storage'], {'array_pct': 69.0, 'array_free_tb': 1.24, 'cache_pct': 22.1, 'drives_ok': True})
        self.assertEqual(s['network'], {'down_mbps': 2342.7, 'up_mbps': 297.9, 'ping_ms': 10.7, 'dns_blocked_pct': 4.4})
        self.assertEqual(s['media'], {'streams': 1, 'now_playing': {'title': 'The Bear', 'episode': 'S3E2', 'user': 'ryan', 'paused': False, 'progress': 0.4}})
        self.assertEqual(s['updates'], {'pending': 2, 'checked_at': 900})
        # Missing or stale sources are null, never zeros; an unreachable update runner leaves updates null.
        bare = self.summary(self.base(updated_at=900), ['router'])
        self.assertEqual((bare['storage'], bare['media'], bare['updates'], bare['network']['down_mbps']), (None, None, None, None))
        bad_drive = self.summary(self.base(unraid={'online': True, 'array_pct': 50, 'last_success_at': 990},
                                           scrutiny={'online': True, 'drives': [{'healthy': False}], 'last_success_at': 990}), ['router'])
        self.assertIs(bad_drive['storage']['drives_ok'], False)
        src = patch_widget_more(patch_widget_strata(patch_widget_dns(patch_widget(self.SRC))))
        self.assertEqual(patch_widget_more(src), src)
        with self.assertRaises(ValueError):
            patch_widget_more('class DashboardHandler: pass\n')

    def test_loaded_strata_counts_as_the_pc_model(self):
        desk = {'online': True, 'last_success_at': 995, 'models': [{'id': 'qwen', 'state': 'unloaded'}], 'is_active': False}
        st = {'online': True, 'last_success_at': 995, 'loaded': True, 'model': 'swift-1.5-iq3_xxs'}
        s = self.summary(self.base(), ['router'], desktop=desk, strata=st)
        self.assertEqual((s['pc']['state'], s['pc']['model'], s['headline']), ('model_loaded', 'swift-1.5-iq3_xxs', 'Model loaded'))
        # Unloaded, stale or unreachable Strata leaves the PC idle; a PC that is off stays off.
        for bad in ({**st, 'loaded': False}, {**st, 'last_success_at': 900}, {**st, 'online': False}):
            self.assertEqual(self.summary(self.base(), ['router'], desktop=desk, strata=bad)['pc']['state'], 'idle')
        self.assertEqual(self.summary(self.base(), ['router'], strata=st)['pc']['state'], 'off')
        # A loaded llama model wins (never both), and the patch is idempotent and refuses drift.
        both = {**desk, 'models': [{'id': 'qwen', 'state': 'loaded'}]}
        self.assertEqual(self.summary(self.base(), ['router'], desktop=both, strata=st)['pc']['model'], 'qwen')
        src = patch_widget_strata(patch_widget(self.SRC))
        self.assertEqual(patch_widget_strata(src), src)
        with self.assertRaises(ValueError):
            patch_widget_strata('class DashboardHandler: pass\n')

    def test_upgrades_v1_in_place_idempotent_and_refuses_drift(self):
        v1 = self.SRC.replace('class DashboardHandler', '# HOMELAB_WIDGET_V1\ndef _dashboard_widget_summary(now=None):\n    return {}\n\nclass DashboardHandler')
        out = patch_widget(v1)
        self.assertNotIn('WIDGET_V1', out); self.assertIn('WIDGET_V2', out); self.assertIn("'pc': pc", out)
        self.assertEqual(patch_widget(out), out)
        with self.assertRaises(ValueError):
            patch_widget('class DashboardHandler(BaseHTTPRequestHandler):\n    pass\n')

class AiLiveTests(unittest.TestCase):
    def test_route_added_once_and_refuses_drift(self):
        src = 'class H:\n    def do_GET(self):\n        if self.path.startswith("/api/telemetry"):\n            pass\n'
        out = patch_ai_live(src)
        self.assertIn('"/api/ai/live"', out)
        self.assertEqual(patch_ai_live(out), out)
        with self.assertRaises(ValueError):
            patch_ai_live('class H: pass\n')


if __name__ == '__main__':
    unittest.main()


class OpenCodeRemovalTests(unittest.TestCase):
    """OpenCode was removed and OpenHands retired 2 Nov 2026, Hermes retired 9 Nov 2026 (pi replaces them): a backend that
    earlier deploys gave 'opencode'/'openhands'/'hermes' checks and .19/.21 rows loses them; pi's row stays."""

    def test_patch_drops_retired_agents(self):
        src = ("def poll_network_and_matrix():\n"
               "    port_targets = {'router': ('192.168.0.1', 80), 'opencode': ('192.168.0.19', 4096), 'openhands': ('192.168.0.19', 4097), 'hermes': ('192.168.0.21', 8787), 'pi': ('192.168.0.21', 8790)}\n"
               "    known = {19: ('OpenHands', 'LXC 114', 'x', False), 21: ('Hermes', 'LXC 116', 'https://hermes.lan', False), 22: ('pi', 'LXC 116', 'https://pi.lan', False)}\n"
               "    for i in [2, 19, 21, 22]:\n"
               "        pass\n")
        from backend_patch import patch_opencode
        out = patch_opencode(src)
        for gone in ("'opencode'", "'openhands'", "OpenHands", "'hermes'", "Hermes"):
            self.assertNotIn(gone, out)
        self.assertIn("'searxng': ('192.168.0.13', 8888)", out)
        for probe in ("'audiobooks': ('192.168.0.4', 13378)", "'rdt': ('192.168.0.13', 6500)",
                      "'flaresolverr': ('192.168.0.13', 8191)", "'files': ('192.168.0.13', 6501)"):
            self.assertIn(probe, out)
        self.assertIn("22: ('pi', 'LXC 116', 'https://pi.lan'", out)
        self.assertIn("for i in [2, 22]", out)
        self.assertEqual(out, patch_opencode(out))  # idempotent


WG_FIXTURE = (
    "def poll_network_and_matrix():\n"
    "    known = {30: ('WireGuard Gateway', 'VM 107', 'http://192.168.0.3:8999', checks.get('wireguard_ssh', False))}\n"
    "    wg_vm_up = any((g.get('vmid') == 107 and g.get('online') for g in TELEMETRY['pve'].get('guests', [])))\n"
    "    checks['wireguard_ssh'] = bool(wg_vm_up or '192.168.0.30' in active_ips)\n"
)


class TestPiWebui(unittest.TestCase):
    PI_FIXTURE = (
        "def poll_network_and_matrix():\n"
        "    port_targets = {'hermes': ('192.168.0.21', 8787), 'searxng': ('192.168.0.13', 8888)}\n"
        "    known = {21: ('Hermes', 'LXC 116', 'https://hermes.lan', checks.get('hermes', False))}\n"
        "    for i in [21]:\n"
        "        ip = f'192.168.0.{i}'\n"
        "        matrix.append({'octet': f'.{i}', 'ip': ip})\n"
    )

    def test_pi_probe_added(self):
        from backend_patch import patch_pi_webui
        out = patch_pi_webui(self.PI_FIXTURE)
        self.assertIn("'pi': ('192.168.0.21', 8790)", out)
        self.assertIn("('pi', 'LXC 116', 'https://pi.lan', checks.get('pi', False))", out)
        self.assertIn("22", out)  # new services row keyed above hermes' 21
        self.assertIn("'192.168.0.21' if i == 22", out)  # row key renders on .21, Hermes' octet
        self.assertIn("'octet': f\".{ip.rsplit('.', 1)[1]}\"", out)  # label from the address, not the key
        self.assertEqual(out, patch_pi_webui(out))  # idempotent

    def test_old_pi_web_ui_port_rewritten(self):
        from backend_patch import patch_pi_webui
        old = (self.PI_FIXTURE
               .replace("port_targets = {'hermes'", "port_targets = {'pi': ('192.168.0.21', 8788), 'hermes'"))
        out = patch_pi_webui(old)
        self.assertIn("'pi': ('192.168.0.21', 8790)", out)
        self.assertNotIn('8788', out)
        self.assertEqual(out, patch_pi_webui(out))

    def test_unknown_probe_shape_raises(self):
        from backend_patch import patch_pi_webui
        with self.assertRaises(AssertionError):
            patch_pi_webui("def poll_network_and_matrix():\n    checks = {}\n")


class TestWireGuardLxc117(unittest.TestCase):
    def test_probe_follows_lxc_117(self):
        from backend_patch import patch_wireguard_lxc117
        out = patch_wireguard_lxc117(WG_FIXTURE)
        self.assertIn("g.get('vmid') in (117, 107)", out)
        self.assertIn("('WireGuard Gateway', 'LXC 117',", out)
        self.assertEqual(out, patch_wireguard_lxc117(out))  # idempotent

    def test_unknown_probe_shape_raises(self):
        from backend_patch import patch_wireguard_lxc117
        with self.assertRaises(ValueError):
            patch_wireguard_lxc117("checks = {}\n")


MAP_FIXTURE = (
    "def poll_network_and_matrix():\n"
    "    checks = {}\n"
    "    port_targets = {'router': ('192.168.0.1', 80), 'wireguard_ssh': ('192.168.0.30', 51820)}\n"
    "    known = {1: ('GL-BE9300 Router', 'Gateway', 'http://192.168.0.1', checks.get('router', False)), "
    "3: ('Proxmox VE 8', 'Hypervisor', 'https://192.168.0.3:8006', checks.get('pve', False)), "
    "30: ('WireGuard Gateway', 'LXC 117', 'http://192.168.0.3:8999', checks.get('wireguard_ssh', False)), "
    "254: ('Macvlan Shim', 'Unraid Route', 'http://192.168.0.4', checks.get('unraid', False)), "
    "255: ('pi', 'LXC 116', 'https://pi.lan', checks.get('pi', False))}\n"
    "    matrix = []\n"
    "    for i in [1, 3, 30, 254, 255]:\n"
    "        ip = '192.168.0.21' if i == 255 else f'192.168.0.{i}'\n"
    "        name, role, url, tcp_ok = known[i]\n"
    "        matrix.append({'octet': f\".{ip.rsplit('.', 1)[1]}\", 'ip': ip, 'name': name, 'role': role, 'url': url, 'online': bool(tcp_ok or ip in active_ips)})\n"
)


class TestNetworkMapRows(unittest.TestCase):
    """Spec 11 Nov 2026 item 2: the map renames two hosts, loses the syslog link on the WireGuard
    row (key 20 in this fixture stands in for the live .30 row), and gains .12, .20 and .22."""

    def test_renames_and_new_rows_applied_once(self):
        from backend_patch import patch_network_map_rows
        out = patch_network_map_rows(MAP_FIXTURE)
        self.assertIn("('Flint 3', 'Gateway'", out)
        self.assertIn("('Proxmox VE 9', 'Hypervisor'", out)
        self.assertNotIn('GL-BE9300', out)
        self.assertNotIn("'Proxmox VE 8'", out)
        self.assertIn("('Gaming PC', 'RTX 5070 Ti', None, False)", out)
        self.assertIn("('Caddy proxy', 'LXC 115', 'https://proxy.lan', False)", out)
        self.assertIn("('Roam 6', 'Living-room Wi-Fi', 'http://192.168.0.22', False)", out)
        self.assertEqual(out, patch_network_map_rows(out))  # second run is a no-op

    def test_rows_render_in_address_order(self):
        from backend_patch import patch_network_map_rows
        out = patch_network_map_rows(MAP_FIXTURE)
        # pi's key (255) is its address .21, so it must sit between .20 and .22, not after .254.
        self.assertIn("for i in [1, 3, 12, 20, 255, 22, 30, 254]", out)
        self.assertIn("'192.168.0.21' if i == 255", out)
        self.assertEqual(out, patch_network_map_rows(out))

    def test_missing_known_table_raises(self):
        from backend_patch import patch_network_map_rows
        with self.assertRaises(AssertionError):
            patch_network_map_rows("def poll_network_and_matrix():\n    checks = {}\n")
