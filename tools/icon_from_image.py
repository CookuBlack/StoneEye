# -*- coding: utf-8 -*-
"""把一张方形插画转成程序图标（assets/app.png + assets/app.ico）。

做三件事：
1. 自动裁掉四周的白边（插画常常带着画布留白，直接缩会显得小一圈）；
2. 按圆角裁 alpha，角上是真透明，贴到任务栏/托盘不会出现白方块；
3. 输出 512 的 png 与 16~256 七个尺寸的 ico。

用法：
    <envs>/python.exe tools/icon_from_image.py [源图] [圆角比例]

不传源图时用 assets/icon-source.jpg；圆角比例默认 0.198（从源图量出来的）。
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets")
PNG_SIZE = 512
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # 圆角蒙版超采样倍数，边缘才不锯齿


def _content_box(img: Image.Image, near_white: int = 720):
    """找出非近白内容的包围盒；整张都是白的就返回全图。"""
    a = np.asarray(img.convert("RGB")).astype(np.int32)
    mask = a.sum(axis=2) < near_white
    if not mask.any():
        return 0, 0, img.width, img.height
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _square_crop(img: Image.Image):
    """以内容中心裁一个正方形，宁可多留一点也别把人家的构图压扁。"""
    x0, y0, x1, y1 = _content_box(img)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    side = max(x1 - x0, y1 - y0)
    # 不让裁切框跑出原图
    side = min(side, img.width, img.height)
    left = max(0, min(img.width - side, int(round(cx - side / 2.0))))
    top = max(0, min(img.height - side, int(round(cy - side / 2.0))))
    return img.crop((left, top, left + side, top + side))


def _rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    big = size * SS
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    r = max(1, int(round(big * radius_ratio)))
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=r, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        OUT_DIR, "icon-source.jpg")
    ratio = float(sys.argv[2]) if len(sys.argv) > 2 else 0.198
    if not os.path.exists(src):
        print("[ERROR] 找不到源图：%s" % src)
        return 1

    img = Image.open(src).convert("RGB")
    crop = _square_crop(img)
    master = crop.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS).convert("RGBA")
    master.putalpha(_rounded_mask(PNG_SIZE, ratio))

    os.makedirs(OUT_DIR, exist_ok=True)
    png_path = os.path.join(OUT_DIR, "app.png")
    master.save(png_path, "PNG")

    # 大尺寸直接用 512 降采样，小尺寸（16/24/32）降得太狠会糊，单独从原图裁
    frames = []
    for s in ICO_SIZES:
        if s <= 64:
            f = crop.resize((s, s), Image.LANCZOS).convert("RGBA")
            f.putalpha(_rounded_mask(s, ratio))
        else:
            f = master.resize((s, s), Image.LANCZOS)
        frames.append(f)
    ico_path = os.path.join(OUT_DIR, "app.ico")
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(f.width, f.height) for f in frames],
                    append_images=frames[:-1])

    print("源图   :", src, img.size, "-> 裁切", crop.size)
    print("png    :", png_path, master.size, os.path.getsize(png_path), "字节")
    print("ico    :", ico_path, ICO_SIZES, os.path.getsize(ico_path), "字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
