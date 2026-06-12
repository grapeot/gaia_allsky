"""把用户 PixInsight 调好色的 RGB 彩色 float FITS 瓦片拆成 R/G/B 三套单通道，
跑 hipsgen 三通道 RGB float 建树 + 合成，出彩色 HiPS（zoom-out 质量正道）。

为什么三通道：hipsgen 的 TILES 把多面 FITS 当标量灰度（颜色会丢，实验证实）。彩色
float HiPS 正道（DSS2 等彩色巡天做法）= 每个 R/G/B 通道各建 float 灰度 HiPS（float
域逐层池化保星点锐度/乳光对比），再 RGB action 合成彩色显示层，合成才量化 8-bit。
部署只留 JPEG/PNG（体积不变）。FITS 域池化避免 8-bit median 压星/糊乳光（zoom-out
质量根因，见 deep-research 结论 + skills/hips_1b_tile_generation.md）。

闭环 workflow：
  1. render_tan_wcs --fits 出 RGB 彩色 float FITS 瓦片（outputs/hips1b_fits_tiles/）
  2. 用户 PixInsight 在 float FITS 上调色（Curves×2 色温 + SCNR），导出回 RGB float FITS
  3. 本脚本：拆通道 → 三通道 hipsgen INDEX TILES（fading 消缝）→ RGB 合成 → HiPS

用法：
  python src/build_rgb_float_hips.py --tiles outputs/hips1b_fits_tiles \
      --out outputs/hips1b_out_rgbfloat
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

import numpy as np
from astropy.io import fits

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = "/opt/homebrew/opt/openjdk@11/bin/java"
JAR = os.path.join(ROOT, "outputs", "tmp_reference_hips", "AladinBeta.jar")


def split_rgb_fits(tiles_dir, work_dir):
    """把每块 RGB 彩色 float FITS（3,H,W）拆成 R/G/B 三套单通道 FITS（带 WCS）。"""
    chans = {c: os.path.join(work_dir, c) for c in ("R", "G", "B")}
    for d in chans.values():
        os.makedirs(d, exist_ok=True)
    files = sorted(glob.glob(os.path.join(tiles_dir, "*.fits")))
    if not files:
        sys.exit(f"{tiles_dir} 里没有 .fits 瓦片")
    for f in files:
        base = os.path.basename(f)
        hdu = fits.open(f)[0]
        cube = hdu.data  # (3,H,W)
        if cube.ndim != 3 or cube.shape[0] != 3:
            sys.exit(f"{base} 不是 (3,H,W) RGB 彩色 FITS（shape={cube.shape}）")
        for i, c in enumerate(("R", "G", "B")):
            out = fits.PrimaryHDU(cube[i].astype(np.float32))
            for k in ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2",
                      "CRPIX1", "CRPIX2", "CDELT1", "CDELT2"):
                if k in hdu.header:
                    out.header[k] = hdu.header[k]
            out.writeto(os.path.join(chans[c], base), overwrite=True)
    print(f"拆通道完成：{len(files)} 块 → R/G/B 各 {len(files)} 单通道 FITS", flush=True)
    return chans


def hipsgen(args_list):
    cmd = [JAVA, "-Xmx80g", "-jar", JAR, "-hipsgen"] + args_list
    print("RUN:", " ".join(args_list), flush=True)
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiles", required=True, help="RGB 彩色 float FITS 瓦片目录")
    ap.add_argument("--out", required=True, help="最终彩色 HiPS 输出目录")
    ap.add_argument("--work", default=None, help="拆通道/各通道 HiPS 的工作目录（默认 out 旁）")
    ap.add_argument("--keep-channels", action="store_true",
                    help="保留各通道 float HiPS（默认合成后删，省空间）")
    args = ap.parse_args()

    work = args.work or (args.out.rstrip("/") + "_work")
    os.makedirs(work, exist_ok=True)

    # 1) 拆通道
    chans = split_rgb_fits(args.tiles, os.path.join(work, "tiles"))

    # 2) 各通道建 float HiPS（INDEX TILES，fading 消缝）
    chan_hips = {}
    for c, d in chans.items():
        out_c = os.path.join(work, f"hips_{c}")
        if os.path.isdir(out_c):
            shutil.rmtree(out_c)
        hipsgen([f"in={d}", f"out={out_c}", f"creator_did={c}", f"obs_title=GaiaMW1B_{c}",
                 "fading=true", "INDEX", "TILES"])
        chan_hips[c] = out_c

    # 3) RGB action 合成彩色显示 HiPS
    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    hipsgen([f"out={args.out}",
             f"inRed={chan_hips['R']}", f"inGreen={chan_hips['G']}", f"inBlue={chan_hips['B']}",
             "creator_did=DuckBro", "obs_title=GaiaMW1B", "RGB"])

    # 4) 注入样式化落地页，覆盖 hipsgen 的简陋默认 index.html。文案唯一来源是
    #    skills/hips_landing_page.html（要改落地页文字只改那一份，老/新两条 workflow
    #    都从它注入，自动同步；见 skills/hips_1b_tile_generation.md step 3.5）。
    landing = os.path.join(ROOT, "skills", "hips_landing_page.html")
    if os.path.isfile(landing):
        shutil.copy(landing, os.path.join(args.out, "index.html"))
        print("落地页 index.html 已注入（来源 skills/hips_landing_page.html）", flush=True)

    # 5) 重建高分辨率 Allsky（否则 zoom-out 糊，见 skills/hips_1b_tile_generation.md 顶部）
    try:
        from rebuild_allsky_hires import rebuild
        rebuild(args.out, order=3, per=256)
    except Exception as e:
        print(f"⚠ Allsky 重建失败（部署前请手动跑 rebuild_allsky_hires.py）：{e}", flush=True)

    if not args.keep_channels:
        for d in chan_hips.values():
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(os.path.join(work, "tiles"), ignore_errors=True)
        print("各通道中间产物已清（--keep-channels 可保留）", flush=True)

    print(f"DONE: 彩色 HiPS → {args.out}（整目录可 rsync 到 yage）", flush=True)


if __name__ == "__main__":
    main()
