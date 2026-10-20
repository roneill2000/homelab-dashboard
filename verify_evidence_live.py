"""Explicit on-host smoke test of the full Homepage Qwen proxy/evidence path."""
import json,os,time,urllib.request
from pathlib import Path

def request(body=None):
    req=urllib.request.Request('http://127.0.0.1/api/qwen',data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-Qwen-Request':'1'})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)

def main():
    os.umask(0o077)
    accepted=request({'prompt':'What is the current Unraid cache free space and its configured minimum-free reserve? Do enabled backup jobs cover every guest? State any gaps.'})
    jobid=accepted['id'];deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        result=request()
        assert result.get('id')==jobid,'Another job replaced this test'
        if result['status'] not in ('queued','loading','generating'):break
        time.sleep(5)
    path=Path('/var/lib/homelab-dashboard/evidence-verification-20261020.json')
    path.write_text(json.dumps(result));path.chmod(0o600)
    assert result['status']=='complete',result.get('error')
    assert result['evidence']['observed_at']==result['context_at']
    assert set(result['evidence']['sources'])==set(result['context_sources'])
    assert 'reserve is 100 GiB' in str(result['evidence']['recorded_configuration'])
    print(json.dumps({'status':result['status'],'sources':result['context_sources'],'evidence_bytes':len(json.dumps(result['evidence']).encode()),'performance':result['performance']}))
if __name__=='__main__':main()
