"""Copy Prowlarr's API key to the dashboard host for manual download search, entirely within the home hosts."""
import subprocess
SCRIPT = r'''
import json,subprocess
read = """
import json,re
key=re.search(r'<ApiKey>(\\w+)</ApiKey>',open('/var/lib/prowlarr/config.xml').read()).group(1)
print(json.dumps(dict(url='http://192.168.0.13:9696',key=key)))
"""
config=subprocess.check_output(['pct','exec','102','--','python3','-c',read],text=True)
assert json.loads(config)['key']
write="""
import pathlib,json,sys,os
p=pathlib.Path('/etc/homelab-dashboard');p.mkdir(mode=0o700,exist_ok=True);p.chmod(0o700)
f=p/'prowlarr.json';fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as out: json.dump(json.load(sys.stdin),out)
f.chmod(0o600)
"""
subprocess.run(['pct','exec','104','--','python3','-c',write],input=config,text=True,check=True)
print('Prowlarr search key configured; it stayed on home hosts.')
'''
if __name__=='__main__':
 subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','python3','-'],input=SCRIPT,text=True,check=True)
