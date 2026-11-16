"""What a 3-turn Ask conversation sends on the A310. Offline: /apply-template and /tokenize are mocked,
the live Ask service (192.168.0.2 / Unraid :11438) is never contacted."""
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))   # log_monitor lives here, as on the dashboard host
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'nightly'))
import qwen_service as q


def storage(now=1000):
    return {'disks': [dict(name='cache', online=True, sampled_at=now, free_bytes=2e9)],
            'activity': {'recovery': {'backup': dict(running=False, observed_at=now)}}}


def template_of(messages):
    """Stands in for the chat template: role markers around every turn, as llama.cpp renders them."""
    out = ''
    for m in messages:
        out += f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n"
    return out + '<|im_start|>assistant\n'


def main():
    turns = [dict(q='What is free on the scratch disk?', a='About 40% free; the cache disk is at 50% used.'),
             dict(q='And what about the cache?', a='Cache: 50% used, reading 3 MB/s, writing 4 MB/s.'),
             dict(q='Is a backup running?', a='No backup is running; the last recovery finished cleanly.')]
    broker = q.Broker()
    broker.job = dict(status='queued', prompt='So is there room for a 2 GB download?', updated_at=1000,
                      target='a310', history=turns)
    asked = []

    def fake_fetch(url, body=None, timeout=None):
        if url.endswith('/api/storage'):
            return storage()
        if url.endswith('/v1/models'):
            return {'data': [{'id': q.r.MODEL}]}
        if url.endswith('/apply-template'):
            asked.append(body['messages'])
            return {'prompt': template_of(body['messages'])}
        if url.endswith('/tokenize'):
            # One token per four characters of the rendered template: an estimate, as the real call is mocked.
            return {'tokens': [0] * (len(body['content']) // 4 + 1)}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Yes, there is room.'}}]}

    lock = mock.Mock()
    with mock.patch.object(q.r, 'fetch', side_effect=fake_fetch), \
         mock.patch.object(q.r.shutil, 'disk_usage', return_value=mock.Mock(free=2**32)), \
         mock.patch.object(q.r, 'stop_model'), mock.patch.object(q.subprocess, 'run'), \
         mock.patch.object(q.time, 'time', return_value=1000):
        broker.work(lock)
        state = broker.state()
    messages = asked[-1]
    print('status             :', state['status'])
    print('turns sent/dropped :', state['history_sent'], '/', state['history_dropped'])
    print('roles sent         :', ' '.join(m['role'] for m in messages))
    print('input tokens       :', state['input_tokens'], 'of input budget', q.INPUT_BUDGET, '(context window', q.A310_LIMIT, ')')
    print('chars sent         :', sum(len(m['content']) for m in messages))
    print('context note       :', state['context_note'])


if __name__ == '__main__':
    main()
