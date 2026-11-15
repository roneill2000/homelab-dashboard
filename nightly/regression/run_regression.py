#!/usr/bin/env python3
"""Nightly-review regression set: the 15 hard round-4 events scored by the round-4 rubric.

Run on Unraid (the A310 box): python3 /mnt/disk1/.homelab-nightly/regression/run_regression.py
Starts the model exactly like runner.py does (same image, args, profile) but under its own
container name and port 11438, holds the same run.lock, never runs inside the blocked windows,
runs each event once at the runner's seed (42), scores 2/1/0 with the rubric rule, prints a
table plus the total, writes results-<date>-<PROMPT_VERSION>.jsonl, publishes it to /mnt/share/ai/nightly-regression/ on
Proxmox over ssh (md5-verified; the /tmp staging copy is then removed; --out keeps the local copy only),
and removes its container.
Scoring only: importing this module does not import the runner (tests import score directly).
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEP = {'ignore': 0, 'watch': 1, 'act': 2}
# Unraid's own A310 rules (AGENTS): nightly at 03:00 and the Ask service both use these windows.
BLOCKED_WINDOWS = [((2, 45), (4, 0)), ((6, 45), (7, 15))]


def blocked_now(now=None):
    now = now or datetime.now()
    return any((h1, m1) <= (now.hour, now.minute) < (h2, m2) for (h1, m1), (h2, m2) in BLOCKED_WINDOWS)


SHARE_DIR = '/mnt/share/ai/nightly-regression'


def publish_to_share(name, local_path, ssh_host='root@192.168.0.3'):
    """Copy the results file to the Proxmox share, verify by md5, then remove the Unraid staging copy."""
    import hashlib
    import subprocess
    raw = local_path.read_bytes()
    try:
        r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', ssh_host,
                            f"mkdir -p {SHARE_DIR} && cat > {SHARE_DIR}/{name}"],
                           input=raw, capture_output=True, timeout=30)
        if r.returncode != 0:
            return f'failed, keeping {local_path}: {(r.stderr or b"").decode("utf-8","replace").strip()[:120]}'
        v = subprocess.run(['ssh', '-o', 'BatchMode=yes', ssh_host, f'md5sum {SHARE_DIR}/{name}'],
                           capture_output=True, timeout=15)
        remote = (v.stdout or b'').decode('ascii','replace').split()[:1]
        if not remote or remote[0] != hashlib.md5(raw).hexdigest():
            return f'pushed but unverified, keeping {local_path}'
        local_path.unlink()
        return f'{SHARE_DIR}/{name} (staging copy removed)'
    except Exception as exc:
        return f'failed, keeping {local_path}: {type(exc).__name__}'


def load_events(path=HERE / 'events.jsonl'):
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len(events) != 15:
        raise ValueError(f'expected 15 events, got {len(events)}')
    return events


def load_expected(path=HERE / 'expected.json'):
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith('_')}


def score(entry, finding):
    """2/1/0 by the round-4 rubric rule, against the event's expectation written before any answer."""
    verdict = str(finding.get('verdict', ''))
    text = f"{finding.get('interpretation', '')}\n{finding.get('next', '')}"
    if verdict in entry.get('forbidden_verdicts', []):
        return 0
    if any(re.search(rx, text, re.I) for rx in entry.get('forbidden_phrases', [])):
        return 0
    acceptable = entry['acceptable_verdicts']
    off = min(abs(STEP[verdict] - STEP[a]) for a in acceptable) if verdict in STEP else 9
    if off >= 2:
        return 0
    key_ok = any(re.search(rx, text, re.I) for rx in entry['key_phrases'])
    if entry.get('key_required') and not key_ok:
        return 0
    out = 2 if off == 0 and key_ok else 1
    if out == 2 and any(re.search(rx, text, re.I) for rx in entry.get('cap1_phrases', [])):
        out = 1
    return out


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', help='results directory (default: <runner root>/regression-results)')
    parser.add_argument('--wait', action='store_true', help='wait (max 60 min) for a blocked window or a busy GPU')
    parser.add_argument('--image', help='override runner.IMAGE for this run (image comparison runs)')
    args = parser.parse_args(argv)
    parent = str(HERE.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)  # installed layout: regression/ sits next to runner.py
    import runner

    if args.image:
        runner.IMAGE = args.image
    runner.NAME = 'homelab-nightly-regression'
    runner.API = 'http://127.0.0.1:11438'
    base_command = runner.container_command

    def container_command(context_size=8192, reasoning_budget=768, profile='quick'):
        command = base_command(context_size, reasoning_budget, profile)
        command[command.index('-p') + 1] = '127.0.0.1:11438:8000'
        return command

    runner.container_command = container_command

    def wait_if_blocked():
        if not blocked_now():
            return True
        if not args.wait:
            print('refusing to run: inside a blocked A310 window (02:45-04:00, 06:45-07:15)')
            return False
        deadline = time.time() + 3600
        while blocked_now() and time.time() < deadline:
            time.sleep(60)
        return not blocked_now()

    def gpu_busy():
        # Wait if another llama/llm container holds the GPU; never kill anything (Jellyfin may merely
        # mount renderD129; only llama-family containers actively hold it).
        import subprocess
        running = subprocess.run(['docker', 'ps', '--format', '{{.Names}} {{.Image}}'],
                                 capture_output=True, text=True, timeout=20).stdout.lower()
        return [w for w in running.splitlines() if re.search(r'llama|llm|a310', w) and runner.NAME not in w]

    def wait_gpu():
        hold = gpu_busy()
        if not hold and not args.wait:
            return True
        deadline = time.time() + (1800 if args.wait else 0)
        while hold and time.time() < deadline:
            print('waiting: GPU held by ' + '; '.join(hold), flush=True)
            time.sleep(30)
            hold = gpu_busy()
        if hold:
            print('refusing to run: GPU held by ' + '; '.join(hold))
        return not hold

    lock = open(runner.ROOT / 'run.lock', 'w')
    try:
        if not runner.acquire_run_lock(lock, scheduled=True, timeout=1200):
            print('nightly runner or Ask service holds the lock; try later')
            return 2
        if not wait_if_blocked() or not wait_gpu():
            return 2
        events, expected = load_events(), load_expected()
        missing = {e['id'] for e in events} ^ set(expected)
        if missing:
            print('events/expected ids differ: ' + ', '.join(sorted(missing)))
            return 2
        print(f'starting model ({runner.MODEL}, {runner.IMAGE})...', flush=True)
        load_seconds = runner.start_model('quick')
        print(f'model loaded in {load_seconds} s', flush=True)
        try:  # one warm-up call so the measured run is not the engine's first answer
            warm = dict(id='warmup', host='warmup', level='WARNING', count=1, first_at=time.time(), last_at=time.time(),
                        message='regression warm-up line', hours=[0] * 24, context=[],
                        current_service_state={'state': 'unverified'})
            runner.review_group(warm, 'quick')
        except Exception as exc:
            print(f'warm-up failed: {type(exc).__name__}')
        results, total = [], 0
        for event in events:
            group, entry = event['group'], expected[event['id']]
            started = time.time()
            finding, usage, reason = runner.review_group(group, 'quick')
            if finding is None:
                record = dict(id=event['id'], score=0, verdict='', what='', next='',
                              failure=f'review_group:{reason}', seconds=round(time.time() - started, 1))
            else:
                finding.setdefault('interpretation', '')
                record = dict(id=event['id'], score=score(entry, finding), verdict=finding['verdict'],
                              what=finding['interpretation'][:400], next=finding.get('next', '')[:200],
                              withheld=finding.get('withheld_reason'), usage=usage,
                              seconds=round(time.time() - started, 1))
                total += record['score']
            results.append(record)
            print(f"{record['id']:<18} {record.get('verdict',''):<7} {record['score']}/2  "
                  f"{record.get('failure') or record['what'][:90]}", flush=True)
        print(f'TOTAL {total}/30  (model {runner.MODEL}, prompt {runner.PROMPT_VERSION}, image {runner.IMAGE})')
        try:  # llama-server's own lines: buffer sizes (VRAM), load time, generation speed
            import subprocess
            logs = subprocess.run(['docker', 'logs', runner.NAME], capture_output=True, text=True, timeout=30).stderr
            for line in logs.splitlines():
                low = line.lower()
                if any(k in low for k in ('offloaded to', 'max VRAM', 'model buffer size',
                                          'compute buffer size', 'eval time for', 'sample time for',
                                          'total time for', 'loaded in')):
                    print(f'server> {line.strip()}')
        except Exception as exc:
            print(f'server log stats: {type(exc).__name__}')
        pt = sum(r.get('usage', {}).get('prompt_tokens', 0) for r in results)
        ct = sum(r.get('usage', {}).get('completion_tokens', 0) for r in results)
        secs = sum(r.get('seconds', 0) for r in results)
        print(f'usage: {pt} prompt + {ct} completion tokens in {secs:.0f} s '
              f'(~{ct / max(secs, 1):.1f} tok/s client-side)')
        out_dir = Path(args.out) if args.out else Path('/tmp/homelab-nightly-regression-results')
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"results-{datetime.now().strftime('%Y%m%d')}-{runner.PROMPT_VERSION}.jsonl"
        body = [json.dumps(dict(r, model=runner.MODEL, prompt_version=runner.PROMPT_VERSION, image=runner.IMAGE,
                                seed=42, ts=int(time.time()), load_seconds=load_seconds)) for r in results]
        path = out_dir / name
        path.write_text('\n'.join(body) + '\n')
        print(f'wrote {path}')
        if not args.out:  # spec: results live on the share, not on Unraid's disk1 or /tmp
            print(f'published to share: {publish_to_share(name, path)}')
        return 0
    finally:
        try:
            runner.stop_model()
            print('model container removed')
        except Exception as exc:
            print(f'container cleanup: {exc}')
        lock.close()


if __name__ == '__main__':
    sys.exit(main())
