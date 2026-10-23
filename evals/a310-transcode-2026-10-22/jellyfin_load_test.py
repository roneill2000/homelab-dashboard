"""Real Jellyfin transcode load test: Jellyfin's own ffmpeg command, N different 4K remuxes at once."""
import json, os, re, shlex, subprocess, sys, threading, time
FILES = ['Saving.Private.Ryan.1998.UHD.BDRemux.2160p.HDR.DoVi.P7.by.DVT.mkv',
         'Blade.Runner.2049.2017.INTERNAL.2160p.REMAKE.DV.HDR10+.TrueHD.DDP.7.1.Atmos-jennaortegaUHD.mkv',
         'Tenet.2020.PROPER.IMAX.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-FGT.mkv',
         'Parasite.2019.KOREAN.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv',
         'The.Dark.Knight.Rises.2012.UHD.BluRay.2160p.DTS-HD.MA.5.1.DV.HEVC.HYBRID.REMUX-FraMeSToR.mkv',
         'Lawrence.of.Arabia.1962.PROPER.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv']
DUR = 60
def cmd(i, n):
    ss = f'00:{20 + i * 7:02d}:{(n * 11) % 60:02d}'
    out = f'/transcode/bench-{n}-{i}'
    return ['docker', 'exec', 'Jellyfin', 'sh', '-c', f'mkdir -p {out} && exec /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -nostats -loglevel error '
        f'-analyzeduration 200M -probesize 1G -ss {ss} -t {DUR} -f matroska -init_hw_device vaapi=va:/dev/dri/renderD129,driver=iHD '
        f'-init_hw_device qsv=qs@va -init_hw_device opencl=ocl@va -filter_hw_device qs -hwaccel vaapi -hwaccel_output_format vaapi '
        f'-noautorotate -i "file:/data/movies/{FILES[i]}" -noautoscale -map_metadata -1 -map_chapters -1 -threads 0 '
        f'-map 0:v:0 -map 0:a:0 -map -0:s -codec:v:0 h264_qsv -low_power 1 -preset veryfast -mbbrc 1 -b:v 54532940 -maxrate 54532941 '
        f'-rc_init_occupancy 109065880 -bufsize 218131760 -g:v:0 72 -keyint_min:v:0 72 '
        f'-vf "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc,procamp_vaapi=b=16,tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709:extra_hw_frames=32,hwmap=derive_device=qsv,format=qsv" '
        f'-codec:a:0 libmp3lame -abr:a 1 -b:a 256000 -ac 2 -copyts -avoid_negative_ts disabled -max_muxing_queue_size 2048 '
        f'-f hls -max_delay 5000000 -hls_time 3 -hls_segment_type mpegts -hls_segment_filename "{out}/s%d.ts" -hls_playlist_type vod -hls_list_size 0 -y "{out}/p.m3u8"']
def cpu():
    a = list(map(int, open('/proc/stat').readline().split()[1:]))
    return sum(a), a[3] + a[4]
def disk_read():
    for l in open('/proc/diskstats'):
        p = l.split()
        if p[2] in ('sdb', 'sdc'): yield p[2], int(p[5]) * 512
def gpu_sampler(stop, samples):
    # The Ask Qwen service on this host already samples the A310 every second.
    import urllib.request
    tok = open('/mnt/disk1/.homelab-nightly/qwen-token').read().strip()
    while not stop.is_set():
        try:
            req = urllib.request.Request('http://192.168.0.4:11438/gpu', headers={'Authorization': 'Bearer ' + tok})
            l = json.load(urllib.request.urlopen(req, timeout=3)).get('latest') or {}
            samples.append({k: l[k] for k in ('video_pct', 'render_pct', 'compute_pct', 'enhance_pct', 'vram_used_mib') if isinstance(l.get(k), (int, float))})
        except Exception:
            pass
        time.sleep(1)
results = []
for n in [int(x) for x in sys.argv[1:]] or range(1, 9):
    subprocess.run(['sync']); open('/proc/sys/vm/drop_caches', 'w').write('1\n')  # no help from cached file data
    t0c, i0c = cpu(); d0 = dict(disk_read()); stop = threading.Event(); gs = []
    g = threading.Thread(target=gpu_sampler, args=(stop, gs)); g.start()
    start = time.time(); procs = [(i, subprocess.Popen(cmd(i, n), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)) for i in range(n)]
    per = []
    for i, p in procs:
        out, err = p.communicate(timeout=900)
        pl = subprocess.run(['docker', 'exec', 'Jellyfin', 'cat', f'/transcode/bench-{n}-{i}/p.m3u8'], capture_output=True, text=True).stdout
        made = sum(float(x) for x in re.findall(r'#EXTINF:([\d.]+)', pl))
        per.append(dict(file=FILES[i].split('.')[0] + ' ' + FILES[i].split('.')[1], seconds=round(time.time() - start, 1), video_s=round(made, 1), ok=p.returncode == 0 and made >= DUR - 5, err=(err.strip().splitlines() or [''])[-1][:160]))
    wall = time.time() - start; stop.set(); g.join(timeout=5)
    t1c, i1c = cpu(); d1 = dict(disk_read())
    busy = 100 * (1 - (i1c - i0c) / max(1, t1c - t0c))
    mbps = {k: round((d1[k] - d0.get(k, 0)) / wall / 1e6, 1) for k in d1}
    engines = {}
    for s in gs:
        for k, v in s.items(): engines.setdefault(k, []).append(v)
    eng = {k: round(max(v)) for k, v in engines.items() if v}
    speeds = [round(x['video_s'] / x['seconds'], 2) for x in per]
    row = dict(streams=n, min_speed=min(speeds), speeds=speeds, cpu_pct=round(busy), disk_MBps=mbps, gpu_peak_busy=eng, failures=[x for x in per if not x['ok']])
    results.append(row); print(json.dumps(row), flush=True)
    subprocess.run(['docker', 'exec', 'Jellyfin', 'sh', '-c', 'rm -rf /transcode/bench-*'])
    if min(speeds) < 0.9: break  # clearly past capacity; stop adding streams
json.dump(results, open('/tmp/jfbench.json', 'w'))
