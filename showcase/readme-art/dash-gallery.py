import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont
S, D = sys.argv[1], sys.argv[2]
def font(size, wght):
    f = ImageFont.truetype(f"{S}/gsflex.ttf", size)
    try: f.set_variation_by_axes([min(size,144), wght])
    except Exception: pass
    return f
def card(img, w, h, radius, bar=0):
    im = img.convert("RGB").resize((w, h), Image.LANCZOS)
    c = Image.new("RGBA", (w, h+bar), (0,0,0,0)); m = Image.new("L", c.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0,0,*c.size), radius=radius, fill=255)
    body = Image.new("RGBA", c.size, (30,36,35,255)); body.paste(im, (0, bar)); c.paste(body, (0,0), m)
    if bar:
        d = ImageDraw.Draw(c)
        for i, col in enumerate([(237,106,94),(245,191,79),(98,197,84)]): d.ellipse((56+i*80, bar//2-24, 104+i*80, bar//2+24), fill=col)
    return c
def caption(canvas, cx, y, label):
    d = ImageDraw.Draw(canvas); f = font(112, 600); tw = d.textlength(label, font=f); x0 = cx - tw/2 - 72
    d.rounded_rectangle((x0, y, x0+tw+144, y+168), radius=84, fill=(31,77,69,255)); d.text((x0+72, y+20), label, font=f, fill=(236,246,242))
def place(canvas, c, x, y):
    sh = Image.new("RGBA", (c.width+160, c.height+160), (0,0,0,0)); sh.paste((0,0,0,110), (80,128), c.split()[3])
    sh = sh.filter(ImageFilter.GaussianBlur(64)); canvas.alpha_composite(sh, (x-80, y-80)); canvas.alpha_composite(c, (x, y))
# desktop: 2 columns x 3 rows
items = [("overview","Overview"),("ai","Local AI"),("storage","Storage"),("energy","Energy"),("infra","Infrastructure"),("network","Network & DNS")]
w, h, bar, gap, pad, capH = 2880, 1800, 128, 180, 180, 260
W = pad*2 + 2*w + gap; H = pad*2 + 3*(h+bar+capH) + 2*72
cv = Image.new("RGBA", (W, H), (0,0,0,0))
for i,(n,label) in enumerate(items):
    x = pad + (i%2)*(w+gap); y = pad + (i//2)*(h+bar+capH+72)
    place(cv, card(Image.open(f"{D}/showcase/{n}.png"), w, h, 64, bar), x, y); caption(cv, x+w//2, y+h+bar+60, label)
cv.save(f"{D}/showcase/gallery-desktop.png", optimize=True, compress_level=9); print("desktop", cv.size)
