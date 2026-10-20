"""Explicit live A310 smoke test. Run on Unraid; stores only test results privately."""
import json
import os
from pathlib import Path
import time
import urllib.request

ROOT=Path('/mnt/disk1/.homelab-nightly')
RESULT=ROOT/'context-verification-20261020.json'
QUESTIONS=[
    'What is the current Unraid RAM use, and which containers are running? Use the supplied observations.',
    'Summarize the recent warning log groups. State the log coverage limits and do not infer present health from historical events.',
    'Explain RAID parity and why it does not replace a backup. This is a general knowledge question.'
]

def request(body=None):
    req=urllib.request.Request('http://192.168.0.4:11438/job',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+(ROOT/'qwen-token').read_text().strip(),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)

def main():
    os.umask(0o077)
    results=[]
    def save():
        stage=RESULT.with_suffix('.tmp');stage.write_text(json.dumps(results));stage.replace(RESULT)
    for question in QUESTIONS:
        accepted=request({'prompt':question});jobid=accepted['id']
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            state=request()
            if state.get('id')!=jobid:raise RuntimeError('Another job replaced validation; stop')
            if state['status'] not in ('queued','loading','generating'):break
            time.sleep(5)
        results.append({k:state.get(k) for k in ('id','status','prompt','answer','error','performance','load_seconds','total_seconds','context_note','context_sources','context_omissions','context_limit')})
        save()
        if state['status']!='complete':break
    print(json.dumps({'tests':len(results),'statuses':[x['status'] for x in results]}))

if __name__=='__main__':main()
