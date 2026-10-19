"""Reuse Sonarr's existing Rdt login, entirely within the home hosts."""
import subprocess
SCRIPT = r'''
import json,pathlib,subprocess
read = """
import json,sqlite3
with sqlite3.connect('file:/var/lib/sonarr/sonarr.db?mode=ro',uri=True) as db:
 rows=db.execute("select Settings from DownloadClients where Implementation='QBittorrent'").fetchall()
 fields=[json.loads(r[0]) for r in rows]
 f=next(f for f in fields if int(f.get('port',0))==6500 and f.get('host') in ('192.168.0.13','localhost','127.0.0.1'))
 print(json.dumps(dict(url='http://192.168.0.13:6500',username=f['username'],password=f['password'])))
"""
config=subprocess.check_output(['pct','exec','102','--','python3','-c',read],text=True)
value=json.loads(config)
assert value['username'] and value['password']
write="""
import pathlib,json,sys,os
p=pathlib.Path('/etc/homelab-dashboard');p.mkdir(mode=0o700,exist_ok=True);p.chmod(0o700)
f=p/'rdt.json';fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as out: json.dump(json.load(sys.stdin),out)
f.chmod(0o600)
"""
subprocess.run(['pct','exec','104','--','python3','-c',write],input=config,text=True,check=True)
print('Rdt collector login configured; credentials stayed on home hosts.')
'''
if __name__=='__main__':
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','python3','-'],input=SCRIPT,text=True,check=True)
