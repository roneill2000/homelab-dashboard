"""The LAN gaming PC's sleep helpers, shared by the dashboard (Wake/Sleep buttons, via update_proxy) and the GPU broker
(broker wake-on-request, 12 Nov 2026). No side effects at import; no daemon threads live here."""
import json
import socket
import subprocess

PC_MAC = '34:5a:60:01:a1:9a'  # gaming PC, Realtek 5GbE (Wake on Magic Packet + Shutdown WoL enabled)


def pc_awake(timeout=1.5):
    """True when the gaming PC answers on its SSH port (a refusal still means it's up); False when asleep or off."""
    try:
        socket.create_connection(('192.168.0.12', 22), timeout=timeout).close()
        return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def sleep_pc(force=False, command=None):
    """Ask the gaming PC to sleep via its forced-command key (60 s warning on screen; refuses if the GPU is busy unless
    forced. command='cancel' instead cancels a sleep countdown in progress."""
    args = ['ssh', '-i', '/etc/homelab-dashboard/pc_sleep_ed25519', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'UserKnownHostsFile=/etc/homelab-dashboard/pc_known_hosts', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=6', 'roneill@192.168.0.12', command or ('force' if force else 'sleep')]
    r = subprocess.run(args, capture_output=True, text=True, timeout=25)
    return json.loads(r.stdout.strip().splitlines()[-1])


def wake_pc():
    """Send a Wake-on-LAN magic packet for the gaming PC to the LAN broadcast addresses."""
    payload = b'\xff' * 6 + bytes.fromhex(PC_MAC.replace(':', '')) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in ('192.168.0.255', '255.255.255.255'):
            for port in (9, 7):
                sock.sendto(payload, (target, port))
