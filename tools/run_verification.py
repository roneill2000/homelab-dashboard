"""Run in LXC112. Persist the disposable chat result across laptop disconnects."""
from pathlib import Path
import subprocess
root=Path('/opt/open-webui/homelab-verification')
try:
    result=subprocess.run(['docker','exec','-i','-w','/app/backend','open-webui','python','-'],
        input=(root/'chat.py').read_text(),text=True,capture_output=True,timeout=150)
    (root/'result.log').write_text(result.stdout+'\n'+result.stderr+'\nRETURN_CODE='+str(result.returncode))
except Exception as error:
    (root/'result.log').write_text(type(error).__name__+': '+str(error))
