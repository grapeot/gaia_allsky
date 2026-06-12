"""把 HiPS 的 Allsky 预览从默认的 64px/tile 重建成高分辨率（默认 256px/tile）。

为什么需要它（zoom-out 糊的真因，调研 + 浏览器 Network 实测确认）：
  Aladin Lite 在大 FOV / 低 Norder（实测 FOV 50-67°）时，显示的不是全分辨率
  512×512 瓦片，而是 HiPS 的 **Allsky 预览文件**——hipsgen 默认把 Norder3 的每个
  512×512 瓦片降到 **64×64** 拼成一张图（8× 损失）。所以 zoom-out 糊 = 你在看 64px
  的粗预览，**和瓦片生成方法（median / float 池化）毫无关系**。早先以为是池化问题、
  折腾了一整轮 float HiPS，实测两条路 raw tile 平均差仅 ~16.5/255、无意义——真因在
  这里。

  修法（最省、改动最小）：用全分辨率 Norder3 瓦片，按同样的 HEALPix-npix 布局重拼
  一个 256px/tile（4×）的 Allsky，覆盖默认的 64px 版。实测 Aladin v2 加载后 zoom-out
  明显变清晰。hipsgen 自己的 ALLSKY action 只出 64px、没有分辨率参数，所以必须手动重建。

布局规则（HiPS 规范）：Allsky 按 npix 顺序排成网格，列数 = floor(sqrt(Ntile_order))，
Norder3 全天 768 tile → 27 列。每个 npix 的格子：row = npix // 27, col = npix % 27。

用法（HiPS 拼好后、部署前跑一次）：
  python src/rebuild_allsky_hires.py --hips outputs/hips1b_out_bsc5   # 默认 256px/tile
  # --per 512 更清晰但 Allsky 文件更大
"""
import argparse
import glob
import os
import re
import shutil

from PIL import Image


def rebuild(hips_dir, order=3, per=256, ncol_override=None):
    nd = os.path.join(hips_dir, f"Norder{order}")
    if not os.path.isdir(nd):
        raise SystemExit(f"{nd} 不存在")
    # 该 order 全天瓦片数 = 12 * 4^order；Allsky 列数 = floor(sqrt(Ntile))
    ntile = 12 * (4 ** order)
    ncol = ncol_override or int(ntile ** 0.5)
    nrow = (ntile + ncol - 1) // ncol

    tiles = {}
    for f in glob.glob(os.path.join(nd, "**", "Npix*.jpg"), recursive=True):
        m = re.search(r"Npix(\d+)\.jpg", os.path.basename(f))
        if m:
            tiles[int(m.group(1))] = f
    if not tiles:
        raise SystemExit(f"{nd} 没有 Npix*.jpg 瓦片")

    canvas = Image.new("RGB", (ncol * per, nrow * per), (0, 0, 0))
    for npix, f in tiles.items():
        r, c = npix // ncol, npix % ncol
        t = Image.open(f).convert("RGB").resize((per, per), Image.LANCZOS)
        canvas.paste(t, (c * per, r * per))

    allsky = os.path.join(nd, "Allsky.jpg")
    if os.path.isfile(allsky) and not os.path.isfile(allsky + ".orig64"):
        shutil.copy(allsky, allsky + ".orig64")  # 备份原 64px 版一次
    canvas.save(allsky, quality=92)
    print(f"重建 Allsky: {len(tiles)} tile 填入, {per}px/tile, "
          f"{ncol}×{nrow} 布局 → {allsky} ({canvas.size[0]}×{canvas.size[1]})", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hips", required=True, help="HiPS 输出目录（含 NorderN）")
    ap.add_argument("--order", type=int, default=3, help="Allsky 基于哪个 Norder（默认 3）")
    ap.add_argument("--per", type=int, default=256, help="每 tile 像素（默认 256，原 64）")
    ap.add_argument("--ncol", type=int, default=None, help="覆盖列数（默认 floor(sqrt(Ntile))）")
    args = ap.parse_args()
    rebuild(args.hips, args.order, args.per, args.ncol)


if __name__ == "__main__":
    main()
