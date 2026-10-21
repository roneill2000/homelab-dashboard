"""Install the explicitly requested LAN auto-login proxy; keep secrets remote."""
import json,pathlib,subprocess
root=pathlib.Path(__file__).parent
password=(pathlib.Path.home()/'Downloads/MEMOS_CREDENTIALS.txt').read_text().split('Password: ',1)[1].strip()
payload={'source':(root/'lan_proxy.py').read_text(),'config':{'username':'roneill','password':password}}
script=r'''
import json,pathlib,subprocess
payload=PAYLOAD
path=pathlib.Path('/usr/local/bin/memos-lan-proxy.py');path.write_text(payload['source']);path.chmod(0o755)
path=pathlib.Path('/etc/memos-auto-login.json');path.write_text(json.dumps(payload['config']));path.chmod(0o600)
subprocess.run(['chown','memos:memos',str(path)],check=True)
unit=pathlib.Path('/etc/systemd/system/memos.service')
unit.write_text(unit.read_text().replace('--addr 0.0.0.0 --port 5230','--addr 127.0.0.1 --port 5231'))
pathlib.Path('/etc/systemd/system/memos-lan.service').write_text("""[Unit]
Description=Memos single-user LAN access
After=memos.service
Requires=memos.service
[Service]
User=memos
Group=memos
ExecStart=/usr/bin/python3 /usr/local/bin/memos-lan-proxy.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
[Install]
WantedBy=multi-user.target
""")
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','memos'],check=True)
subprocess.run(['systemctl','enable','--now','memos-lan'],check=True)
print('Memos LAN auto-login enabled; authenticated backend is loopback-only.')
'''.replace('PAYLOAD',repr(payload))
subprocess.run(['ssh','-o','BatchMode=yes','root@192.168.0.3','pct','exec','113','--','python3','-'],input=script,text=True,check=True)
