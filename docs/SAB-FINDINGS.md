# SABnzbd throughput investigation — 19 October 2026

Ryan reported roughly 45 MB/s over a 2.5 Gbps home connection and said SABnzbd has never saturated it. Read-only observations during this session point to scratch-disk contention as a contributor. No download settings, mounts, or storage placement were changed.

Sources: SABnzbd queue/config API, Proxmox/LXC mount inspection, `/proc/diskstats`, `/proc/pressure/io`, process I/O counter deltas and open-file checks, SMART, and NIC counters. These are bounded observations from the session, not a sustained benchmark or proof of the SSD's maximum capability.

- SABnzbd 5.1.3 ran in LXC 102 on `.13:8085`, with no configured speed cap; observed download rates varied from about 45 to 71 MB/s.
- Temporary and completed downloads both use `/mnt/NAS/Prowlarr/...`, a CIFS mount of `//192.168.0.3/n100`. That maps to `/mnt/share` on Proxmox, backed by `sda`, a T-FORCE 1 TB SATA SSD.
- RdtClient was writing to the same share at approximately 54.34 MB/s in a process-counter sample. Its open writable files confirmed the same filesystem.
- A 10-second disk-counter sample measured approximately 98.55 MB/s written to sda, 99.9% busy time, 496.67 ms average I/O wait, and average queue depth 12.81. Concurrent Samba process writes were approximately 71.91 MB/s in a separate sample; these intervals are not interchangeable.
- The separate NVMe device was lightly loaded in the disk sample: 1.22 MB/s written, 4.1% busy, 0.4 ms average wait.
- Host I/O pressure was substantial (full PSI about 48%; CPU iowait samples 27–46%). CPU throttling and memory pressure did not explain the observed slowdown.
- The 2.5 Gbps NIC negotiated full duplex and had no RX/TX errors in the inspected counters. A lifetime dropped-packet count alone was not used to diagnose packet loss.
- SMART passed and the SATA SSD was 33°C. This does not establish an SSD hardware failure. There was no active unpacking process in the inspected interval.

Inference: simultaneous downloads to the SATA scratch disk, through a loopback SMB path, can constrain SABnzbd. A controlled comparison with RdtClient idle would better quantify the effect; moving temporary downloads to a faster local disk/bind mount is another option for a separately authorized storage change. Provider speed, SSD cache exhaustion, and SMB overhead have not been isolated independently.

The dashboard now exposes scratch read/write history to help observe this contention. Its Glances feed does not expose the busy/queue metrics above; those values came from the diagnostic kernel-counter sample.
