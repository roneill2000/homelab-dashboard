# Trust the homelab certificate (once per device)
macOS: double-click homelab-root-ca.crt → Keychain Access opens → find "Caddy Local Authority" in System → double-click → Trust → "Always Trust" → close (password).
Windows: open PowerShell as administrator → certutil -addstore Root homelab-root-ca.crt
Android: copy the file to the phone → Settings → Security & privacy → More security → Encryption & credentials → Install a certificate → CA certificate.
iPhone: AirDrop the file → Settings → Profile Downloaded → Install → Settings → General → About → Certificate Trust Settings → turn on "Caddy Local Authority".
Some apps (for example the Jellyfin Android app) ignore user-installed certificates: keep using http://192.168.0.17:8096 or http://jellyfin.lan:8096 in those apps.