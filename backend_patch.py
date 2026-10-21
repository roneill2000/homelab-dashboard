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
