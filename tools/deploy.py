"""Provision restricted SSH diagnostics, then install the broker on LXC112."""
from pathlib import Path
import json,subprocess
ROOT=Path(__file__).parent

def run(host,command,input=None):
    return subprocess.check_output(['ssh','-o','BatchMode=yes','root@'+host,command],input=input,text=True)
def ct(script):
    return run('192.168.0.3','pct exec 112 -- python3 -',script)
ct("""import os,pathlib,pwd,secrets,subprocess
try: user=pwd.getpwnam('homelab-tools')
except KeyError:
 subprocess.run(['useradd','--system','--home-dir','/var/lib/homelab-tools','--shell','/usr/sbin/nologin','homelab-tools'],check=True)
 user=pwd.getpwnam('homelab-tools')
state=pathlib.Path('/var/lib/homelab-tools');state.mkdir(exist_ok=True);state.chmod(0o700);os.chown(state,user.pw_uid,user.pw_gid)
key=state/'id_ed25519'
if not key.exists(): subprocess.run(['runuser','-u','homelab-tools','--','ssh-keygen','-q','-t','ed25519','-N','','-C','homelab-readonly-diagnostics','-f',str(key)],check=True)
token=state/'token'
if not token.exists(): token.write_text(secrets.token_urlsafe(32))
token.chmod(0o600);os.chown(token,user.pw_uid,user.pw_gid)
""")
key=run('192.168.0.3','pct exec 112 -- cat /var/lib/homelab-tools/id_ed25519.pub').strip()
known=[]
for ip,role,path in [('192.168.0.3','proxmox','/usr/local/libexec/homelab-diagnostics.sh'),('192.168.0.4','unraid','/boot/config/homelab-tools/diagnostics.sh')]:
    pub=run(ip,'cat /etc/ssh/ssh_host_ed25519_key.pub').split()
    known.append(ip+' '+pub[0]+' '+pub[1])
    payload={'path':path,'source':(ROOT/'diagnostics.sh').read_text(),'entry':f'from="192.168.0.16",restrict,command="/usr/bin/timeout 15 /bin/bash {path} {role}" '+key}
    if role=='proxmox':
        script="""import pathlib,json
p=PAYLOAD
f=pathlib.Path(p['path']);f.parent.mkdir(parents=True,exist_ok=True);f.write_text(p['source']);f.chmod(0o755)
a=pathlib.Path('/root/.ssh/authorized_keys')
existing=a.read_text()
if p['entry'] not in existing:
 with a.open('a') as out: out.write('\\n'+p['entry']+'\\n')
print('Restricted diagnostic key installed on Proxmox')
""".replace('PAYLOAD',repr(payload))
        print(run(ip,'python3 -',script).strip())
    else:
        # Unraid includes Python3; fail before changing authorization if it is absent.
        script="""import pathlib
p=PAYLOAD
f=pathlib.Path(p['path']);f.parent.mkdir(parents=True,exist_ok=True);f.write_text(p['source']);f.chmod(0o755)
a=pathlib.Path('/root/.ssh/authorized_keys')
existing=a.read_text()
if p['entry'] not in existing:
 with a.open('a') as out: out.write('\\n'+p['entry']+'\\n')
print('Restricted diagnostic key installed persistently on Unraid')
""".replace('PAYLOAD',repr(payload))
        print(run(ip,'python3 -',script).strip())
unit='''[Unit]
Description=Read-only homelab diagnostic tools
After=network-online.target docker.service
Wants=network-online.target
[Service]
User=homelab-tools
Group=homelab-tools
StateDirectory=homelab-tools
StateDirectoryMode=0700
ExecStart=/usr/bin/python3 /usr/local/bin/homelab-tools.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
[Install]
WantedBy=multi-user.target
'''
payload={'broker':(ROOT/'broker.py').read_text(),'known':'\n'.join(known)+'\n','unit':unit}
script="""import pathlib,os,pwd,subprocess
p=PAYLOAD
f=pathlib.Path('/usr/local/bin/homelab-tools.py');f.write_text(p['broker']);f.chmod(0o755)
f=pathlib.Path('/var/lib/homelab-tools/known_hosts');f.write_text(p['known']);f.chmod(0o600)
u=pwd.getpwnam('homelab-tools');os.chown(f,u.pw_uid,u.pw_gid)
pathlib.Path('/etc/systemd/system/homelab-tools.service').write_text(p['unit'])
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','homelab-tools'],check=True)
subprocess.run(['systemctl','restart','homelab-tools'],check=True)
print('Read-only broker installed on Docker bridge only')
""".replace('PAYLOAD',repr(payload))
print(ct(script).strip())
