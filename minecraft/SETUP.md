# Minecraft (Unraid `itzg-minecraft-server`, 192.168.0.25 on br0)

Paper (`VERSION=LATEST`, Paper 26.3 on Temurin OpenJDK 25 LTS) with Geyser + Floodgate so Java and Bedrock play together. MCXboxBroadcast shows the server on Xbox friends lists.

| Setting | Value | Why |
| --- | --- | --- |
| Java | TCP 25565 | Router forward `Minecraft-Java` |
| Bedrock (RakNet) | UDP 19132 | Old Bedrock clients, until 27 Oct 2026 |
| Bedrock (NetherNet) | signaling TCP 19132, WebRTC UDP 19133 | Bedrock 26.60+ (27 Oct 2026) only connects this way. Geyser `transport: both`, `webrtc-port: 19133`, built-in signaling. Router forwards `Minecraft-Bedrock` (tcp udp 19132) and `Minecraft-Bedrock-NetherNet` (udp 19133) |
| Geyser `auth-type` | floodgate | Bedrock players join with their Xbox account, no Java account needed |
| `enforce-secure-profile` | false | Bedrock players' chat is unsigned |
| `USE_AIKAR_FLAGS` + `JVM_XX_OPTS` | `true` + `-XX:+UseCompactObjectHeaders -XX:+UseStringDeduplication` | Tuned G1GC + Java 25 64-bit compact object headers to fit ~15-20% more chunk/entity data inside the i7-7700T's 8 MB L3 cache |
| Direct Cache SSD + Async I/O | `/mnt/cache/appdata/minecraft/` + `SYNC_CHUNK_WRITES=false` | Bypasses Unraid's single-threaded FUSE (`shfs`) layer; batches region writes on Paper I/O threads |
| Paper Moonrise Threads | `worker-threads: 6`, `io-threads: 2` (`config/paper-global.yml`) | Unlocks all 8 threads of the 4C/8T Core i7-7700T (up from Paper's default 2 workers / 1 I/O), boosting chunk gen 5.6x-8.8x (`448-705 cps`) |
| `simulation-distance` | 8 (view 10) | Less ticking per player; you still see 10 chunks |
| Paper Tick & Network Tuning | `redstone-implementation: ALTERNATE_CURRENT`, `network-compression-threshold: 512`, `hide-online-players: true`, projectile save caps (`arrow: 16`, `snowball: 16`, `fireball: 8`) | Up to 10x fewer redstone block updates, lower LAN packet CPU overhead, blocks scanner bots from scraping players |
| Chunky + ChunkyBorder (`MODRINTH_PROJECTS=chunky,chunkyborder`) | Borders: `50,000m` Overworld/End, `6,250m` Nether (1:8 ratio); Pre-gen: `5,000m` Overworld, `1,000m` Nether (100%), `1,500m` End (100%); `continue-on-restart: true` | Pre-baked core regions on SSD + fast 6-thread Moonrise generation out to 50,000m without overflowing `/mnt/cache` |
| Server Operator (`ops.json`) | `Balf6746` (Level 4 OP) | In-game admin/teleport/chunky access |
| Memory | 6 GB heap | Upgraded to 6 GB on 24 Oct 2026; nightly log review runs Gemma 4 E4B exclusively on the A310 so host RAM pressure is eliminated |

23 Oct 2026: new world started; the previous one is `appdata/minecraft/world-old-20261023` (restore: stop the container, swap the folders back). Backups of the edited files are `*.bak-20261023` and `*.bak-20261025`.
