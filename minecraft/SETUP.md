# Minecraft (Unraid `itzg-minecraft-server`, 192.168.0.25 on br0)

Paper (VERSION=LATEST) with Geyser + Floodgate so Java and Bedrock play together. MCXboxBroadcast shows the server on Xbox friends lists.

| Setting | Value | Why |
| --- | --- | --- |
| Java | TCP 25565 | Router forward `Minecraft-Java` |
| Bedrock (RakNet) | UDP 19132 | Old Bedrock clients, until 27 Oct 2026 |
| Bedrock (NetherNet) | signaling TCP 19132, WebRTC UDP 19133 | Bedrock 26.60+ (27 Oct 2026) only connects this way. Geyser `transport: both`, `webrtc-port: 19133`, built-in signaling. Router forwards `Minecraft-Bedrock` (tcp udp 19132) and `Minecraft-Bedrock-NetherNet` (udp 19133) |
| Geyser `auth-type` | floodgate | Bedrock players join with their Xbox account, no Java account needed |
| `enforce-secure-profile` | false | Bedrock players' chat is unsigned |

23 Oct 2026: new world started; the previous one is `appdata/minecraft/world-old-20261023` (restore: stop the container, swap the folders back). Backups of the edited files are `*.bak-20261023`.
