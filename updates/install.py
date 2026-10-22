"""Install the update runner on Proxmox: compile locally and remotely, then swap atomically."""
import py_compile
import subprocess
from pathlib import Path

SRC = Path(__file__).with_name('homelab_update.py')
py_compile.compile(str(SRC), doraise=True)
remote = ('set -e; t=/usr/local/sbin/.homelab-update.new; cat > "$t"; '
          'python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$t"; '
          'chmod 755 "$t"; mv "$t" /usr/local/sbin/homelab-update; /usr/local/sbin/homelab-update status >/dev/null; echo installed')
subprocess.run(['ssh', '-o', 'BatchMode=yes', 'root@192.168.0.3', remote], input=SRC.read_text(encoding='utf-8'), text=True, check=True)
