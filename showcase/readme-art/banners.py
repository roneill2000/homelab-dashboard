# Both README/social banners. Usage: banners.py <scratch> <android repo> <dashboard repo> <K>
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
S, R, D, K = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
def k(v): return int(round(v * K))
def font(size, wght):
    f = ImageFont.truetype(f"{S}/gsflex.ttf", k(size))
    try: f.set_variation_by_axes([min(k(size), 144), wght])
    except Exception: pass
    return f
def background(glow_box):
    W, H = k(1280), k(640)
    y = Image.linear_gradient("L").resize((W, H)); x = Image.linear_gradient("L").rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT).resize((W, H))
    t = Image.blend(x, y, 0.45)
    bg = Image.composite(Image.new("RGB", (W, H), (31, 77, 69)), Image.new("RGB", (W, H), (12, 30, 28)), t)
    g = Image.new("L", (W, H), 0); ImageDraw.Draw(g).ellipse(tuple(k(v) for v in glow_box), fill=90)
    g = g.filter(ImageFilter.GaussianBlur(k(125)))
    return Image.composite(Image.new("RGB", (W, H), (120, 200, 180)), bg, g.point(lambda v: v // 3))
def chips(d, x, y, labels, fs=21, h=42):
    for c in labels:
        f = font(fs, 600); w = d.textlength(c, font=f) + k(34)
        d.rounded_rectangle((k(x), k(y), k(x) + w, k(y + h)), radius=k(h / 2), fill=(41, 92, 82), outline=(80, 140, 125), width=max(1, K))
        d.text((k(x) + k(17), k(y + (h - fs) / 2 - 2)), c, font=f, fill=(220, 240, 234)); x += (w / K) + 11
def shadow_paste(bg, img, xy, blur=22, off=12, alpha=150):
    m = k(50); sh = Image.new("RGBA", (img.width + 2 * m, img.height + 2 * m), (0, 0, 0, 0))
    sh.paste((0, 0, 0, 255), (m, m + k(off)), img.split()[3].point(lambda v: alpha if v else 0))
    sh = sh.filter(ImageFilter.GaussianBlur(k(blur))); bg.paste(sh, (xy[0] - m, xy[1] - m), sh); bg.paste(img, xy, img)
def phone(path, h, r_out=34, border=8):
    im = Image.open(path).convert("RGB"); w = int(im.width * k(h) / im.height); im = im.resize((w, k(h)), Image.LANCZOS)
    fr = Image.new("RGBA", (w + 2 * k(border), k(h) + 2 * k(border)), (0, 0, 0, 0))
    m = Image.new("L", fr.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, *fr.size), radius=k(r_out), fill=255)
    fr.paste(Image.new("RGBA", fr.size, (18, 20, 22, 255)), (0, 0), m)
    sm = Image.new("L", im.size, 0); ImageDraw.Draw(sm).rounded_rectangle((0, 0, *im.size), radius=k(r_out - 8), fill=255)
    fr.paste(im, (k(border), k(border)), sm); return fr
# ---- Android banner
bg = background((700, -200, 1500, 600)); d = ImageDraw.Draw(bg)
icon = Image.open(f"{R}/assets/icon.png").convert("RGBA").resize((k(132), k(132)), Image.LANCZOS); bg.paste(icon, (k(72), k(96)), icon)
d.text((k(72), k(250)), "Homelab", font=font(96, 700), fill=(240, 247, 244))
d.text((k(76), k(380)), "Android companion for the", font=font(32, 500), fill=(198, 226, 218))
d.text((k(76), k(422)), "Homelab Command Center", font=font(32, 500), fill=(198, 226, 218))
chips(d, 76, 482, ["Quick Settings", "Widget", "Alerts", "Local AI"])
O = f"{S}/orig"
for p, h, xy in [(f"{O}/ai.png", 470, (782, 128)), (f"{O}/widget.png", 446, (1020, 156)), (f"{O}/overview.png", 520, (900, 70))]:
    shadow_paste(bg, phone(p, h), (k(xy[0]), k(xy[1])), alpha=150)
bg.save(f"{R}/screenshots/" + ("social-preview.png" if K == 1 else "banner@2x.png"), optimize=True); print("android", bg.size)
# ---- Dashboard banner
bg = background((650, -250, 1550, 650)); d = ImageDraw.Draw(bg)
logo = Image.open(f"{S}/banner/dash-logo-big.png").convert("RGBA").resize((k(120), k(120)), Image.LANCZOS); bg.paste(logo, (k(64), k(100)), logo)
d.text((k(64), k(244)), "Homelab", font=font(58, 700), fill=(240, 247, 244))
d.text((k(64), k(316)), "Command Center", font=font(58, 700), fill=(240, 247, 244))
d.text((k(66), k(414)), "One live dashboard for Proxmox,", font=font(26, 500), fill=(198, 226, 218))
d.text((k(66), k(450)), "Unraid, DNS, backups and local AI", font=font(26, 500), fill=(198, 226, 218))
chips(d, 68, 512, ["Real-time", "Local AI", "Alerts", "Material 3"], fs=20, h=40)
shot = Image.open(f"{D}/showcase/overview.png").convert("RGB"); sw = k(600); shh = int(shot.height * sw / shot.width); shot = shot.resize((sw, shh), Image.LANCZOS)
bar = k(30); win = Image.new("RGBA", (sw, shh + bar), (0, 0, 0, 0))
m = Image.new("L", win.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, *win.size), radius=k(14), fill=255)
body = Image.new("RGBA", win.size, (30, 36, 35, 255)); body.paste(shot, (0, bar)); win.paste(body, (0, 0), m)
wd = ImageDraw.Draw(win)
for i, c in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]): wd.ellipse((k(14 + i * 20), k(10), k(26 + i * 20), k(22)), fill=c)
wd.rounded_rectangle((k(120), k(7), k(420), k(24)), radius=k(8), fill=(48, 56, 54)); wd.text((k(134), k(8)), "homelab.lan", font=font(12, 500), fill=(170, 190, 185))
shadow_paste(bg, win, (k(636), k(116)), blur=24, off=14)
ph = Image.open(f"{D}/showcase/phone.png").convert("RGB")
shadow_paste(bg, phone(f"{D}/showcase/phone.png", 380, r_out=30, border=7), (k(1072), k(222)), blur=20, alpha=170)
bg.save(f"{D}/showcase/" + ("banner.png" if K == 1 else "banner@2x.png"), optimize=True); print("dashboard", bg.size)
