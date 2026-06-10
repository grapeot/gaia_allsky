"""在已保存的线性浮点画布上专调 tone mapping，秒级迭代，不重渲星表。

render_fov.py --save-linear 存的是 add_skyglow 后、归一化前的线性画布。这里读它，
只跑显示链（adapt_sky_floor → stretch → finish_sky_adapted），暴露压高光的关键
杠杆，sweep 出一排对比，专门把 G<20 裂隙两侧过曝的亮云压回、露出云里暗带细节、
凸显裂隙对比。

压高光主杠杆是 target_white（软肩膝点）：调低→更多亮云高光进入 exp 软肩压缩区，
高光被压且保留纹理（不是硬截断），暗部不动。配合 star_contrast 控整体拉伸。

用法：
  python src/tone_iterate.py --linear outputs/fov_g20_linear.npy \
      --out outputs/_tone_sweep.png --sweep-white 2.0,1.4,1.0,0.7
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_bortle_eye_grid as beg


def tone(canvas, target_sky, star_contrast, target_white, chroma, white_pct=99.5):
    ad = beg.adapt_sky_floor(canvas, target_sky, 25.0, star_contrast)
    stretch = beg.signal_stretch_for_adapted(ad, target_sky, white_pct, target_white)
    rgb = beg.finish_sky_adapted(ad, target_sky, 2.2, target_white, stretch, chroma)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--linear", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target-sky", type=float, default=0.012)
    ap.add_argument("--star-contrast", type=float, default=6.0)
    ap.add_argument("--chroma", type=float, default=1.8)
    ap.add_argument("--sweep-white", default="2.0,1.4,1.0,0.7",
                    help="逗号分隔的 target_white 列表，越小压高光越狠。")
    ap.add_argument("--single", type=float, default=None,
                    help="只出单张：指定一个 target_white。")
    args = ap.parse_args()

    canvas = np.load(args.linear)
    from PIL import Image, ImageDraw

    if args.single is not None:
        arr = tone(canvas, args.target_sky, args.star_contrast, args.single, args.chroma)
        Image.fromarray(arr).save(args.out)
        print(f"wrote {args.out} (target_white={args.single})")
        return

    whites = [float(x) for x in args.sweep_white.split(",")]
    panels = []
    for w in whites:
        arr = tone(canvas, args.target_sky, args.star_contrast, w, args.chroma)
        im = Image.fromarray(arr)
        d = ImageDraw.Draw(im)
        d.text((8, 8), f"target_white={w}", fill="yellow")
        panels.append(im)
    H = panels[0].height
    gap = 8
    W = sum(p.width for p in panels) + gap * (len(panels) - 1)
    out = Image.new("RGB", (W, H), "black")
    x = 0
    for p in panels:
        out.paste(p, (x, 0)); x += p.width + gap
    out.save(args.out)
    print(f"wrote {args.out}  ({len(whites)} 版 sweep: {whites})")


if __name__ == "__main__":
    main()
