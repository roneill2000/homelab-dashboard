"""Pure, bounded backend accuracy migration; credentials stay in remote memory."""
import ast

MARKER = '# HOMELAB_ACCURACY_V1'
HELPERS = '''
def _dashboard_number(value, digits=0):
    import math
    try:
        number = float(value) if value is not None else None
        return round(number, digits) if number is not None and math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None

def _dashboard_observed(path, payload=None, online=True):
    keys = path.split('.')
    previous = TELEMETRY
    for key in keys:
        previous = previous.get(key, {})
    if not isinstance(previous, dict):
        previous = {}
    result = dict(previous if payload is None else payload)
    stamp = int(time.time())
    result.update(online=bool(online), last_attempt_at=stamp,
                  last_success_at=stamp if online else previous.get('last_success_at'),
                  error=None if online else 'Source poll failed')
    return result

def _dashboard_dns_cluster():
    with STATE_LOCK:
        dns = TELEMETRY['pihole']
        nodes = [dns.get('primary', {}), dns.get('secondary', {})]
        available = all(n.get('online') is True for n in nodes)
        flags = [n.get('blocking') for n in nodes]
        blocking = True if available and all(v is True for v in flags) else False if available and all(v is False for v in flags) else None
        status = 'protected' if blocking is True else 'paused' if blocking is False else 'mixed' if available else 'unknown'
        dns.update(blocking=blocking, status=status)
        TELEMETRY['pihole'] = _dashboard_observed('pihole', dns, available)
'''


def telemetry_path(node):
    keys = []
    while isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        keys.insert(0, node.slice.value)
        node = node.value
    return keys if isinstance(node, ast.Name) and node.id == 'TELEMETRY' else None


def expression(text):
    return ast.parse(text, mode='eval').body


class CollectorPatch(ast.NodeTransformer):
    def __init__(self, function_name):
        self.function_name = function_name

    def visit_Call(self, node):
        self.generic_visit(node)
        if self.function_name == 'poll_dns_and_speed' and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and len(node.args) == 2:
            if isinstance(node.args[0], ast.Constant) and node.args[0].value == '_blocking':
                node.args[1] = ast.Constant(None)
            elif isinstance(node.args[1], ast.Constant) and node.args[1].value in (2195333, 18):
                node.args[1] = ast.Constant(None)
        return node

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if self.function_name == 'poll_media_and_syslog' and telemetry_path(node) == ['media', 'radarr']:
            node.slice = ast.Constant('readarr')
        return node

    def visit_Dict(self, node):
        self.generic_visit(node)
        if self.function_name == 'poll_unraid_and_scrutiny':
            for i, key in enumerate(node.keys):
                if isinstance(key, ast.Constant) and key.value == 'healthy':
                    value = node.values[i]
                    if isinstance(value, ast.Compare) and isinstance(value.left, ast.Call):
                        reading = value.left
                        reading.args = reading.args[:1]
                        node.values[i] = ast.IfExp(test=ast.Compare(left=reading, ops=[ast.IsNot()], comparators=[ast.Constant(None)]), body=value, orelse=ast.Constant(None))
        return node

    def visit_Assign(self, node):
        self.generic_visit(node)
        if len(node.targets) != 1:
            return node
        target = node.targets[0]
        name = target.id if isinstance(target, ast.Name) else None
        sensors = {'cpu_temp', 'nvme_temp', 'gpu_temp', 'gpu_fan_rpm'}
        scratch = {'scratch_pct', 'scratch_free_gb', 'scratch_total_gb'}
        if self.function_name in {'poll_proxmox', 'poll_unraid_and_scrutiny'}:
            if name in sensors:
                if isinstance(node.value, ast.Constant):
                    node.value = ast.Constant(None)
                else:
                    # Existing assignments are round(float(sensor.get('value', fallback))).
                    gets = [n for n in ast.walk(node.value) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get']
                    if len(gets) != 1:
                        raise ValueError('Unexpected sensor expression')
                    reading = gets[0]
                    reading.args = reading.args[:1]
                    node.value = ast.Call(func=ast.Name('_dashboard_number', ast.Load()), args=[reading], keywords=[])
            elif name in scratch and isinstance(node.value, ast.Constant):
                node.value = ast.Constant(None)
        path = telemetry_path(target)
        if not path:
            return node
        if path[-1] == 'online' and isinstance(node.value, ast.Constant) and node.value.value is False:
            parent = target.value
            return ast.copy_location(ast.Assign(targets=[parent], value=ast.Call(func=ast.Name('_dashboard_observed', ast.Load()), args=[ast.Constant('.'.join(path[:-1]))], keywords=[ast.keyword(arg='online', value=ast.Constant(False))])), node)
        eligible = {'pve', 'unraid', 'scrutiny', 'speedtest', 'syslog', 'netalertx', 'services', 'weather'}
        watched = len(path) == 1 and path[0] in eligible or len(path) == 2 and path[0] == 'media' or path in [['pihole', 'primary'], ['pihole', 'secondary']]
        if watched:
            if path == ['media', 'readarr'] and isinstance(node.value, ast.Dict):
                node.value.keys = [ast.Constant('books') if isinstance(k, ast.Constant) and k.value == 'movies' else k for k in node.value.keys]
            node.value = ast.Call(func=ast.Name('_dashboard_observed', ast.Load()), args=[ast.Constant('.'.join(path)), node.value], keywords=[])
        return node


def patch_backend(source):
    """Return patched source without executing it, logging it, or writing it."""
    if MARKER in source:
        compile(source, '<dashboard>', 'exec')
        return source
    tree = ast.parse(source)
    names = {'poll_proxmox', 'poll_unraid_and_scrutiny', 'poll_dns_and_speed', 'poll_media_and_syslog', 'poll_network_and_matrix'}
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    missing = names - functions.keys()
    if missing:
        raise ValueError('Unsupported backend: collector functions missing')
    edits = []
    for name in names:
        old = functions[name]
        start, end = old.lineno, old.end_lineno
        node = CollectorPatch(name).visit(old)
        if name == 'poll_dns_and_speed':
            node.body.append(ast.Expr(value=ast.Call(func=ast.Name('_dashboard_dns_cluster', ast.Load()), args=[], keywords=[])))
        ast.fix_missing_locations(node)
        edits.append((start, end, ast.unparse(node) + '\n'))
    # The legacy Readarr collector mislabeled its result as Radarr. Remove only
    # that initializer entry; this does not discover or invent a Radarr service.
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TELEMETRY' for t in node.targets) and isinstance(node.value, ast.Dict):
            changed = False
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == 'media' and isinstance(value, ast.Dict):
                    for i, media_key in enumerate(value.keys):
                        if isinstance(media_key, ast.Constant) and media_key.value == 'radarr':
                            value.keys[i] = ast.Constant('readarr')
                            value.values[i] = expression("{'online': False, 'books': None, 'queue': None}")
                            changed = True
            if changed:
                ast.fix_missing_locations(node)
                edits.append((node.lineno, node.end_lineno, ast.unparse(node) + '\n'))
    lines = source.splitlines(keepends=True)
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start-1:end] = [replacement]
    # Helpers precede all function definitions and any service start calls.
    result = ''.join(lines)
    insertion = next(n.lineno for n in ast.parse(result).body if isinstance(n, ast.FunctionDef))
    lines = result.splitlines(keepends=True)
    lines[insertion-1:insertion-1] = [MARKER + '\n' + HELPERS + '\n']
    result = ''.join(lines)
    compile(result, '<dashboard>', 'exec')
    return result

LOG_MARKER = '# HOMELAB_FULL_LOG_V1'
LOG_HELPER = '''
def _dashboard_log_details(status):
    import json, math, urllib.request
    result = dict(status, latest_event=None, last_alert_full=False)
    try:
        with urllib.request.urlopen('http://192.168.0.3:8999/api/alerts', timeout=3) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            return result
        events = json.loads(raw)
        if not isinstance(events, list):
            return result
        candidates = [e for e in events[:100] if isinstance(e, dict) and isinstance(e.get('msg'), str)
                      and isinstance(e.get('ts'), (int, float)) and math.isfinite(e['ts'])]
        if candidates:
            event = max(candidates, key=lambda e: e['ts'])
            result.update(last_alert=event['msg'], last_alert_full=True,
                          latest_event={k:event.get(k) for k in ('msg','host','level','ts')})
    except Exception:
        pass
    return result
'''


def patch_log_details(source):
    """Fetch the bounded full-entry endpoint before acquiring the state lock."""
    if LOG_MARKER in source:
        return source
    tree = ast.parse(source)
    edits = []
    class LogPatch(ast.NodeTransformer):
        matched = 0
        def visit_Assign(self, node):
            self.generic_visit(node)
            value = node.value
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'fetch_json'
                    and value.args and isinstance(value.args[0], ast.Constant)
                    and value.args[0].value == 'http://192.168.0.3:8999/api/status'
                    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                self.matched += 1
                name = node.targets[0].id
                return [node, ast.Assign(targets=[ast.Name(name, ast.Store())], value=expression('_dashboard_log_details('+name+')'))]
            return node
        def visit_Dict(self, node):
            self.generic_visit(node)
            for k,v in zip(node.keys,node.values):
                if isinstance(k,ast.Constant) and k.value=='last_alert' and isinstance(v,ast.Call) and isinstance(v.func,ast.Attribute):
                    base=ast.unparse(v.func.value)
                    node.keys += [ast.Constant('latest_event'),ast.Constant('last_alert_full')]
                    node.values += [expression(base+".get('latest_event')"),expression(base+".get('last_alert_full', False)")]
                    break
            return node
    for old in tree.body:
        if isinstance(old,ast.FunctionDef) and old.name in ('poll_media_and_syslog','task_syslog'):
            transformer=LogPatch();new=transformer.visit(old)
            if transformer.matched:
                ast.fix_missing_locations(new)
                edits.append((old.lineno,old.end_lineno,ast.unparse(new)+'\n'))
    if not edits:
        raise ValueError('Expected syslog collector not found')
    lines=source.splitlines(keepends=True)
    for start,end,replacement in sorted(edits,reverse=True): lines[start-1:end]=[replacement]
    result=''.join(lines)
    insertion=next(n.lineno for n in ast.parse(result).body if isinstance(n,ast.FunctionDef))
    lines=result.splitlines(keepends=True);lines[insertion-1:insertion-1]=[LOG_MARKER+'\n'+LOG_HELPER+'\n']
    result=''.join(lines);compile(result,'<dashboard>','exec')
    return result


def patch_log_groups(source):
    marker='# HOMELAB_LOG_GROUPS_V1'
    if marker in source:return source
    tree=ast.parse(source);edits=[]
    for old in tree.body:
        if isinstance(old,ast.FunctionDef) and old.name=='_dashboard_log_details':
            edits.append((old.lineno,old.end_lineno,'def _dashboard_log_details(status):\n    import log_monitor\n    return log_monitor.details(status)\n'))
        elif isinstance(old,ast.FunctionDef) and old.name in ('task_syslog','poll_media_and_syslog'):
            changed=False
            for node in ast.walk(old):
                if isinstance(node,ast.Dict):
                    for key,value in zip(node.keys,node.values):
                        if isinstance(key,ast.Constant) and key.value=='latest_event' and isinstance(value,ast.Call) and isinstance(value.func,ast.Attribute):
                            base=ast.unparse(value.func.value)
                            node.keys += [ast.Constant('log_groups'),ast.Constant('log_window')]
                            node.values += [expression(base+".get('log_groups')"),expression(base+".get('log_window')")]
                            changed=True;break
            if changed:
                ast.fix_missing_locations(old);edits.append((old.lineno,old.end_lineno,ast.unparse(old)+'\n'))
    if len(edits)<2:raise ValueError('Expected full log collector not found')
    lines=source.splitlines(keepends=True)
    for start,end,value in sorted(edits,reverse=True):lines[start-1:end]=[value]
    result=''.join(lines)+'\n'+marker+'\n';compile(result,'<dashboard>','exec');return result


def patch_qwen(source):
    marker='# HOMELAB_QWEN_V1'
    if marker in source:return source
    tree=ast.parse(source)
    handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DashboardHandler')
    edits=[]
    for method in handler.body:
        if isinstance(method,ast.FunctionDef) and method.name in ('do_GET','do_POST'):
            post=method.name=='do_POST'
            edits.append((method.body[0].lineno-1,
                          '        import qwen_proxy\n        if qwen_proxy.handle(self, post='+str(post)+'): return\n'))
    if len(edits)!=2:raise ValueError('Expected GET and POST handlers')
    lines=source.splitlines(keepends=True)
    for index,code in sorted(edits,reverse=True):lines[index:index]=[code]
    result=''.join(lines)+'\n'+marker+'\n';compile(result,'<dashboard>','exec');return result


def patch_memos(source):
    """Add Memos to the existing bounded TCP monitor and host inventory."""
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'poll_network_and_matrix')
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == 'port_targets':
                assert isinstance(node.value, ast.Dict)
                if 'memos' not in [getattr(k, 'value', None) for k in node.value.keys]:
                    node.value.keys.append(ast.Constant('memos'))
                    node.value.values.append(expression("('192.168.0.18', 5230)"))
                found.add(name)
            elif name == 'known':
                assert isinstance(node.value, ast.Dict)
                if 18 not in [getattr(k, 'value', None) for k in node.value.keys]:
                    node.value.keys.append(ast.Constant(18))
                    node.value.values.append(expression("('Memos', 'LXC 113', 'http://192.168.0.18:5230', checks.get('memos', False))"))
                found.add(name)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'i' and isinstance(node.iter, ast.List):
            if 18 not in [getattr(v, 'value', None) for v in node.iter.elts]:
                node.iter.elts.append(ast.Constant(18))
                node.iter.elts.sort(key=lambda v: v.value)
            found.add('matrix')
    assert found == {'port_targets', 'known', 'matrix'}, found
    lines = source.splitlines(keepends=True)
    lines[fn.lineno-1:fn.end_lineno] = [ast.unparse(ast.fix_missing_locations(fn))+'\n']
    return ''.join(lines)


ACTION_GUARD = '''
def _dashboard_action_allowed(handler):
    import qwen_proxy
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        length = -1
    if not qwen_proxy.same_origin(handler, 'X-Dashboard-Action'):
        code, message = 403, 'Use the homepage to run this action.'
    elif not 0 <= length <= 8192:
        code, message = 400, 'Invalid request body.'
    else:
        return True
    raw = json.dumps({'ok': False, 'error': message}).encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
    return False

def _dashboard_timer(data):
    try:
        return max(1, min(3600, int(data.get('timer', 300))))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 300
'''


def patch_actions(source):
    """Same-origin guard for action POSTs, bounded DNS pause, same-origin telemetry."""
    marker = '# HOMELAB_ACTION_GUARD_V1'
    if marker in source:
        return source
    replacements = [
        ('            self.send_header("Access-Control-Allow-Origin", "*")\n', ''),
        ('        if self.path == "/api/action/pihole-toggle":\n',
         '        if self.path.startswith("/api/action/") and not _dashboard_action_allowed(self): return\n'
         '        if self.path == "/api/action/pihole-toggle":\n'),
        ('            enable = bool(req_data.get("enable", True))\n',
         '            enable = bool(req_data.get("enable", True)) if isinstance(req_data, dict) else True\n'),
        ('            timer = int(req_data.get("timer", 300))\n',
         '            timer = _dashboard_timer(req_data)\n'),
        ('"timer": None if enable_bool else int(timer_sec)}',
         '"timer": None if enable_bool else _dashboard_timer({"timer": timer_sec})}'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError('Expected action handler not found: ' + old.strip()[:60])
        source = source.replace(old, new)
    source = source.replace('class DashboardHandler(', ACTION_GUARD + '\n' + marker + '\nclass DashboardHandler(', 1)
    compile(source, '<dashboard>', 'exec')
    return source


def patch_updates(source):
    """Route /api/updates to update_proxy ahead of every other handler."""
    marker = '# HOMELAB_UPDATES_V1'
    if marker in source:
        return source
    for method in ('GET', 'POST'):
        old = f'        import qwen_proxy\n        if qwen_proxy.handle(self, post={method == "POST"}): return\n'
        if source.count(old) != 1:
            raise ValueError('Expected Qwen route in do_' + method)
        source = source.replace(old, old + f'        import update_proxy\n        if update_proxy.handle(self, post={method == "POST"}): return\n')
    source += '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


def patch_jellyfin_auth(source):
    """Jellyfin 12 rejects the legacy X-Emby-Token header; send the token as an Authorization header instead.

    The token value stays where it already is on the server; only the header shape changes.
    """
    import re
    marker = '# HOMELAB_JELLYFIN_AUTH_V1'
    if marker in source:
        return source
    pattern = re.compile(r"\{'X-Emby-Token': '([0-9a-f]{32})'\}")
    if len(pattern.findall(source)) != 1:
        raise ValueError('Expected one Jellyfin token header')
    source = pattern.sub(lambda m: "{'Authorization': 'MediaBrowser Token=\"%s\"'}" % m.group(1), source)
    source += '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


def patch_opencode(source):
    """Add the OpenCode web UI (LXC 114) to the TCP monitor and host inventory, like patch_memos."""
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'poll_network_and_matrix')
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == 'port_targets':
                if 'opencode' not in [getattr(k, 'value', None) for k in node.value.keys]:
                    node.value.keys.append(ast.Constant('opencode'))
                    node.value.values.append(expression("('192.168.0.19', 4096)"))
                found.add(name)
            elif name == 'known':
                if 19 not in [getattr(k, 'value', None) for k in node.value.keys]:
                    node.value.keys.append(ast.Constant(19))
                    node.value.values.append(expression("('OpenCode', 'LXC 114', 'http://192.168.0.19:4096', checks.get('opencode', False))"))
                found.add(name)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'i' and isinstance(node.iter, ast.List):
            if 19 not in [getattr(v, 'value', None) for v in node.iter.elts]:
                node.iter.elts.append(ast.Constant(19))
                node.iter.elts.sort(key=lambda v: v.value)
            found.add('matrix')
    assert found == {'port_targets', 'known', 'matrix'}, found
    lines = source.splitlines(keepends=True)
    lines[fn.lineno-1:fn.end_lineno] = [ast.unparse(ast.fix_missing_locations(fn))+'\n']
    return ''.join(lines)


def patch_speedtest_latest_completed(source):
    """Show the newest *completed* Speedtest Tracker result; one failed run (e.g. Ookla NoServersException) used to blank the card."""
    import re
    marker = '# HOMELAB_SPEEDTEST_COMPLETED_V1'
    if marker in source:
        return source
    pattern = re.compile(r"st = fetch_json\('http://192\.168\.0\.14/api/v1/results/latest', headers=(\{[^}]*\})\)\.get\('data', \{\}\)")
    if len(pattern.findall(source)) != 1:
        raise ValueError('Expected one Speedtest Tracker fetch')
    source = pattern.sub(lambda m: "st = next((r for r in fetch_json('http://192.168.0.14/api/v1/results?page[size]=10&sort=-created_at', headers=" + m.group(1) + ").get('data', []) if r.get('status') == 'completed'), None)", source)  # None -> AttributeError -> card shows unavailable, never zeros
    source += '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


def patch_speedtest_time(source):
    """Expose when the shown Speedtest result ran, so an old result can't pass as current."""
    marker = '# HOMELAB_SPEEDTEST_TIME_V1'
    if marker in source:
        return source
    old = "'server': st.get('data', {}).get('server', {}).get('name', 'Comcast')}"
    if source.count(old) != 1:
        raise ValueError('Expected one Speedtest result dict')
    source = source.replace(old, "'server': st.get('data', {}).get('server', {}).get('name', 'Comcast'), 'tested_at': st.get('created_at')}")
    source += '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


def patch_remove_kasm(source):
    """Remove Kasm (LXC 103, 192.168.0.15) from port targets, known hosts, and IP matrix."""
    marker = '# HOMELAB_REMOVE_KASM_V1'
    if marker in source:
        return source
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'poll_network_and_matrix')
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == 'port_targets':
                pairs = [(k, v) for k, v in zip(node.value.keys, node.value.values) if getattr(k, 'value', None) != 'kasm']
                node.value.keys = [p[0] for p in pairs]
                node.value.values = [p[1] for p in pairs]
            elif name == 'known':
                pairs = [(k, v) for k, v in zip(node.value.keys, node.value.values) if getattr(k, 'value', None) != 15]
                node.value.keys = [p[0] for p in pairs]
                node.value.values = [p[1] for p in pairs]
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'i' and isinstance(node.iter, ast.List):
            node.iter.elts = [elt for elt in node.iter.elts if getattr(elt, 'value', None) != 15]
    lines = source.splitlines(keepends=True)
    lines[fn.lineno-1:fn.end_lineno] = [ast.unparse(ast.fix_missing_locations(fn))+'\n']
    source = ''.join(lines) + '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


def patch_pihole_vip(source):
    """Expose Pi-hole VRRP floating VIP (192.168.0.8) in pihole telemetry, port_targets, and ip_matrix."""
    marker = '# HOMELAB_PIHOLE_VIP_V1'
    if marker in source:
        return source
    if "'vip': '192.168.0.8'" not in source:
        source = source.replace("'secondary': {'ip': '192.168.0.10'", "'vip': '192.168.0.8', 'secondary': {'ip': '192.168.0.10'", 1)
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'poll_network_and_matrix'), None)
    if fn is not None:
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == 'port_targets' and isinstance(node.value, ast.Dict):
                    if not any(getattr(k, 'value', None) == 'pihole_vip' for k in node.value.keys):
                        node.value.keys.append(ast.Constant('pihole_vip'))
                        node.value.values.append(ast.parse("('192.168.0.8', 53)", mode='eval').body)
                elif name == 'known' and isinstance(node.value, ast.Dict):
                    if not any(getattr(k, 'value', None) == 8 for k in node.value.keys):
                        node.value.keys.append(ast.Constant(8))
                        node.value.values.append(ast.parse("('Pi-hole DNS VIP', 'VRRP Floating IP', 'http://192.168.0.11/admin', checks.get('pihole_vip', False))", mode='eval').body)
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'i' and isinstance(node.iter, ast.List):
                vals = [getattr(e, 'value', None) for e in node.iter.elts]
                if 8 not in vals and 6 in vals:
                    idx = vals.index(6) + 1
                    node.iter.elts.insert(idx, ast.Constant(8))
        lines = source.splitlines(keepends=True)
        lines[fn.lineno-1:fn.end_lineno] = [ast.unparse(ast.fix_missing_locations(fn)) + '\n']
        source = ''.join(lines)
    source += '\n' + marker + '\n'
    compile(source, '<dashboard>', 'exec')
    return source


# Same mark as brand-icon.svg (dark-theme primary-container / on-primary-container). Keep the two in sync.
# Dark-theme surface (generated, seed #1f4d45, Fidelity, spec 2025): manifest theme/background colour.
BRAND_SURFACE = '#091613'
BRAND_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="128" fill="#1f4d45"/><path d="M256 112 L404 230 Q412 236 412 246 V372 Q412 400 384 400 H128 Q100 400 100 372 V246 Q100 236 108 230 Z" fill="#8ebdb2" stroke="#8ebdb2" stroke-width="16" stroke-linejoin="round"/><rect x="152" y="252" width="208" height="34" rx="12" fill="#1f4d45"/><circle cx="330" cy="269" r="8" fill="#8ebdb2"/><rect x="152" y="302" width="208" height="34" rx="12" fill="#1f4d45"/><circle cx="330" cy="319" r="8" fill="#8ebdb2"/><rect x="152" y="352" width="208" height="34" rx="12" fill="#1f4d45"/><circle cx="330" cy="369" r="8" fill="#8ebdb2"/></svg>'


def patch_m3_brand(source):
    """Material 3 app icon (brand-icon.svg) for /icon.svg and manifest colours from the generated M3 dark surface.
    Idempotent and re-runnable: it rewrites the icon and colours every deploy, so palette changes reach the server."""
    import re
    source, n = re.subn(r'ICON_SVG = """.*?"""', lambda m: 'ICON_SVG = """' + BRAND_ICON_SVG + '"""', source, count=1, flags=re.S)
    assert n == 1, 'ICON_SVG not found'
    source = re.sub(r'"(background_color|theme_color)": "#[0-9a-fA-F]{6}"', lambda m: '"' + m.group(1) + '": "' + BRAND_SURFACE + '"', source)
    if '# HOMELAB_M3_BRAND_V1' not in source:
        source += '\n# HOMELAB_M3_BRAND_V1\n'
    compile(source, '<dashboard>', 'exec')
    return source


COMPRESSION_HELPERS = '''
def _dashboard_etag(body):
    import hashlib
    return '"' + hashlib.sha256(body).hexdigest()[:16] + '"'

_DASHBOARD_GZIP = {}

def _dashboard_gzip(body, etag=None):
    import gzip
    if etag is None:
        return gzip.compress(body, compresslevel=6, mtime=0)
    if etag not in _DASHBOARD_GZIP:
        _DASHBOARD_GZIP.clear()
        _DASHBOARD_GZIP[etag] = gzip.compress(body, compresslevel=9, mtime=0)
    return _DASHBOARD_GZIP[etag]

'''

COMPRESSION_SEND = '''    def _dashboard_send(self, body, content_type, cache_control, etag=None):
        gz = 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) > 1024
        if etag:
            sent = [t.strip().replace('-gz"', '"') for t in self.headers.get('If-None-Match', '').split(',')]
            if etag in sent:
                self.send_response(304)
                self.send_header('ETag', etag[:-1] + '-gz"' if gz else etag)
                self.send_header('Cache-Control', cache_control)
                self.send_header('Vary', 'Accept-Encoding')
                self.end_headers()
                return
        if gz:
            body = _dashboard_gzip(body, etag)
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', cache_control)
        self.send_header('Vary', 'Accept-Encoding')
        if etag:
            self.send_header('ETag', etag[:-1] + '-gz"' if gz else etag)
        if gz:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

'''


def patch_compression(source):
    """Gzip the page and React bundles, and give the bundles an ETag so a reload revalidates with a 304.
    The page itself stays no-cache: the server bakes live telemetry into it on every request."""
    marker = '# HOMELAB_COMPRESSION_V1'
    if marker in source:
        return source
    html = ('            self.send_response(200)\n'
            '            self.send_header("Content-Type", "text/html; charset=utf-8")\n'
            '            self.send_header("Cache-Control", "no-cache")\n'
            '            self.end_headers()\n'
            '            self.wfile.write(body)\n')
    static = ('                self.send_response(200)\n'
              '                self.send_header("Content-Type", "application/javascript; charset=utf-8")\n'
              '                self.send_header("Cache-Control", "public, max-age=86400")\n'
              '                self.end_headers()\n'
              '                self.wfile.write(body)\n')
    handler = 'class DashboardHandler(BaseHTTPRequestHandler):\n'
    for needle in (html, static, handler):
        if source.count(needle) != 1:
            raise ValueError('dashboard handler shape changed; compression patch not applied')
    source = source.replace(html, '            self._dashboard_send(body, "text/html; charset=utf-8", "no-cache")\n')
    source = source.replace(static, '                self._dashboard_send(body, "application/javascript; charset=utf-8", "public, max-age=86400", _dashboard_etag(body))\n')
    source = source.replace(handler, marker + '\n' + COMPRESSION_HELPERS + handler + COMPRESSION_SEND)
    compile(source, '<dashboard>', 'exec')
    return source


FONTS_ROUTE = r'''        elif self.path.startswith("/static/fonts/"):
            import re, os
            m = re.fullmatch(r"/static/fonts/([a-z0-9-]+\.woff2)", self.path)
            if not m:
                self.send_error(404, "Not Found")
                return
            fonts_dir = os.environ.get("HOMELAB_FONTS_DIR", "/opt/homelab-dashboard/fonts")
            fpath = os.path.join(fonts_dir, m.group(1))
            if os.path.isfile(fpath):
                with open(fpath, "rb") as f:
                    body = f.read()
                self._dashboard_send(body, "font/woff2", "public, max-age=31536000, immutable", _dashboard_etag(body))
                return
            self.send_error(404, "Not Found")
            return
'''


def patch_fonts(source):
    """Serve immutable woff2 font files from /opt/homelab-dashboard/fonts/ with strict allowlist."""
    marker = '# HOMELAB_FONTS_V1'
    if marker in source:
        return source
    target = '        elif self.path in ("/static/react.js", "/static/react-dom.js"):\n'
    if target not in source:
        raise ValueError('static file route shape changed; fonts patch not applied')
    replacement = marker + '\n' + FONTS_ROUTE + target
    source = source.replace(target, replacement, 1)
    compile(source, '<dashboard>', 'exec')
    return source


INLINE_SAFE_OLD = '''            rendered = INDEX_HTML.replace(
                "/*__INITIAL_TELEMETRY_SLOT__*/",
                f"window.__INITIAL_TELEMETRY__ = {initial_json};"
            )
'''

INLINE_SAFE_NEW = r'''            # HOMELAB_INLINE_SAFE_V1: telemetry carries raw syslog text. Escape it so no log line can close the
            # inline <script>, and allow only this response's own scripts (nonce) in case anything slips through.
            for raw, safe in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
                initial_json = initial_json.replace(raw, safe)
            import secrets
            nonce = secrets.token_urlsafe(16)
            csp = f"script-src 'nonce-{nonce}'; object-src 'none'; base-uri 'none'"
            rendered = INDEX_HTML.replace("<script", f'<script nonce="{nonce}"').replace(
                "<head>", f'<head><meta http-equiv="Content-Security-Policy" content="{csp}">', 1
            ).replace(
                "/*__INITIAL_TELEMETRY_SLOT__*/",
                f"window.__INITIAL_TELEMETRY__ = {initial_json};"
            )
'''


def patch_inline_safe(source):
    """Close the stored-XSS path from syslog text into the page: escape the inlined telemetry and add a nonce CSP.
    The nonce is applied to INDEX_HTML before telemetry is inserted, so injected text can never carry it."""
    if '# HOMELAB_INLINE_SAFE_V1' in source:
        return source
    if source.count(INLINE_SAFE_OLD) != 1:
        raise ValueError('page render block changed; inline-safety patch not applied')
    source = source.replace(INLINE_SAFE_OLD, INLINE_SAFE_NEW)
    compile(source, '<dashboard>', 'exec')
    return source


HARDEN_HELPERS = '''
def _dashboard_host_ok(handler):
    # DNS rebinding guard: answer only for LAN IPs, single-label names and .lan names, never an outside domain that
    # has been pointed at this address.
    import ipaddress
    host = (handler.headers.get('Host') or '').strip().lower()
    host = host[1:host.index(']')] if host.startswith('[') else host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return host == 'localhost' or '.' not in host or host.endswith('.lan')

'''


def patch_hardening(source):
    """Red-team fixes: frame protection on the page and bundles (a <meta> CSP cannot set frame-ancestors), a Host check
    against DNS rebinding on every request, and a socket timeout so slow clients cannot hold the server's threads."""
    marker = '# HOMELAB_HARDENING_V1'
    if marker in source:
        return source
    handler = 'class DashboardHandler(BaseHTTPRequestHandler):\n'
    send = "        self.send_header('Content-Length', str(len(body)))\n"
    get, post = '    def do_GET(self):\n', '    def do_POST(self):\n'
    for needle in (handler, send, get, post):
        if source.count(needle) != 1:
            raise ValueError('dashboard handler shape changed; hardening patch not applied')
    guard = "        if not _dashboard_host_ok(self):\n            self.send_error(421, 'Misdirected request')\n            return\n"
    source = source.replace(handler, marker + '\n' + HARDEN_HELPERS + handler + '    timeout = 60\n')
    source = source.replace(send, "        self.send_header('X-Frame-Options', 'DENY')\n"
                                  "        self.send_header('Content-Security-Policy', \"frame-ancestors 'none'\")\n"
                                  "        self.send_header('X-Content-Type-Options', 'nosniff')\n" + send)
    source = source.replace(get, get + guard).replace(post, post + guard)
    compile(source, '<dashboard>', 'exec')
    return source


WIDGET_HELPERS = r'''
def _dashboard_widget_summary(now=None):
    # Same rules as the app's serviceState()/hero (src/app.jsx): readings older than 45 s are stale, a missing reading
    # is unknown (never zero), and services with no monitor are not counted.
    import json, time as _time
    now = _time.time() if now is None else now
    try:
        ids = json.load(open('/opt/homelab-dashboard/catalog.json', encoding='utf-8'))
    except Exception:
        ids = []
    with STATE_LOCK:
        t = json.loads(json.dumps(TELEMETRY))
    updated = t.get('updated_at')
    fresh = isinstance(updated, (int, float)) and now - updated < 45
    def obj(v):
        return v if isinstance(v, dict) else {}
    media, ai, services = obj(t.get('media')), obj(t.get('ai')), obj(t.get('services'))
    direct = {'openwebui': ai.get('webui'), 'pve': t.get('pve'), 'unraid': t.get('unraid'), 'scrutiny': t.get('scrutiny'),
              'syslog': t.get('syslog'), 'netalertx': t.get('netalertx'), 'sabnzbd': media.get('sabnzbd'),
              'sonarr': media.get('sonarr'), 'readarr': media.get('readarr'), 'prowlarr': media.get('prowlarr'),
              'jellyfin': media.get('jellyfin')}
    def state(sid):
        if not updated:
            return 'unknown'
        source = direct.get(sid)
        value = source.get('online') if isinstance(source, dict) and source.get('online') is not None else services.get(sid)
        if value is None:
            return None
        if now - updated > 45:
            return 'stale'
        if isinstance(source, dict) and isinstance(source.get('last_success_at'), (int, float)) and now - source['last_success_at'] > 45:
            return 'stale'
        if not isinstance(source, dict) and isinstance(services.get('last_success_at'), (int, float)) and now - services['last_success_at'] > 45:
            return 'stale'
        return value
    states = [state(i) for i in ids]
    known = [s for s in states if s is not None and s != 'unknown']
    up = sum(1 for s in known if s is True)
    down = sum(1 for s in known if s is False)
    def source_fresh(s):
        s = obj(s)
        last = s.get('last_success_at')
        return s.get('online') is True and ('last_success_at' not in s or (isinstance(last, (int, float)) and now - last < 45)) and not s.get('error')
    pihole, net, sys_ = obj(t.get('pihole')), obj(t.get('netalertx')), obj(t.get('syslog'))
    dns = pihole.get('status') if fresh and pihole.get('status') in ('protected', 'paused', 'mixed') else 'unknown'
    def number(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None
    if not fresh:
        status, headline = 'no_reading', 'No reading'
    elif down:
        status, headline = 'attention', f"{down} {'service' if down == 1 else 'services'} down"
    elif dns in ('paused', 'mixed'):
        status, headline = 'attention', 'DNS blocking paused' if dns == 'paused' else 'DNS blocking mixed'
    else:
        status, headline = 'all_up', 'All services up'
    return {'status': status, 'headline': headline,
            'services_up': up if fresh else None, 'services_known': len(known) if fresh else None,
            'dns': dns,
            'devices_connected': number(net.get('connected')) if source_fresh(net) else None,
            'devices_total': number(net.get('total')) if source_fresh(net) else None,
            'log_errors_24h': number(sys_.get('errors_24h')) if fresh and source_fresh(sys_) else None,
            'updated_at': updated if isinstance(updated, (int, float)) else None}

'''


def patch_widget(source):
    """GET /api/widget: the home-screen widget's compact summary, computed with the app's rules (catalog.json from build)."""
    marker = '# HOMELAB_WIDGET_V1'
    if marker in source:
        return source
    handler = 'class DashboardHandler(BaseHTTPRequestHandler):\n'
    route_at = '        if self.path.startswith("/api/telemetry"):\n'
    for needle in (handler, route_at):
        if source.count(needle) != 1:
            raise ValueError('dashboard handler shape changed; widget patch not applied')
    route = ('        if self.path == "/api/widget":\n'
             '            body = json.dumps(_dashboard_widget_summary()).encode("utf-8")\n'
             '            self.send_response(200)\n'
             '            self.send_header("Content-Type", "application/json")\n'
             '            self.send_header("Cache-Control", "no-store")\n'
             '            self.end_headers()\n'
             '            self.wfile.write(body)\n'
             '            return\n')
    source = source.replace(handler, marker + '\n' + WIDGET_HELPERS + handler)
    source = source.replace(route_at, route + route_at)
    compile(source, '<dashboard>', 'exec')
    return source
