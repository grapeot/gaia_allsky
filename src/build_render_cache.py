"""把分段星表 + 逐星消光合成正式渲染缓存，烘焙截断补偿增益的逐星衰减。

背景（2026-06-09，裂隙发灰问题）：截断补偿增益假设"不可分辨族群和可见暗星
挨同样的消光"，在尘埃方向这是幸存者偏差——缝里留在星表里的多是尘埃前面的
前景星，而增益要代理的 G>13 背景族群几乎全在尘埃后面。修法分两步：

1. 每 1° 天区格取 G>=11 星实测 A_G（gaiadr3.ag_gspphot）的 p90 作全柱消光
   A_col：深处幸存星带出全柱测量，规避幸存样本均值的前景偏置。
2. 每颗暗星代理的背景光按"它身后那段尘埃" A_col - A_G(star) 衰减：
   atten = 10^(-0.4 * max(A_col - A_G, 0))。前景星身后是整条柱（衰减最狠），
   深处星身后没剩多少尘埃（衰减趋一）。

A_G 缺失（GSP-Phot 无解，约 1/3）用所在格的平滑均值插补。直接观测的星光
不受任何影响——衰减只作用于增益推断出来的不可分辨光。

用法：
  python src/build_render_cache.py            # 生成 data/raw/gaia_g13_render.npz
"""
import argparse
import os

import numpy as np
from scipy.ndimage import gaussian_filter


RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
SEGMENTS = ["gaia_ag_0_11.npz", "gaia_ag_11_12.npz", "gaia_ag_12_12p5.npz",
            "gaia_ag_12p5_12p8.npz", "gaia_ag_12p8_13.npz"]
OUT = os.path.join(RAW, "gaia_g13_render.npz")


def load_segments(paths):
    arrs = {k: [] for k in ["l", "b", "g", "bp_rp", "ag"]}
    for p in paths:
        d = np.load(p)
        n = len(d["g"])
        if n == 3000000:
            raise SystemExit(f"{p} 行数恰为 300 万，疑似被 Gaia 匿名查询上限截断，重取该段")
        for k in arrs:
            arrs[k].append(np.asarray(d[k], float))
    return {k: np.concatenate(v) for k, v in arrs.items()}


def cell_indices(l, b):
    li = np.clip((l % 360).astype(int), 0, 359)
    bi = np.clip((b + 90).astype(int), 0, 179)
    return li, bi


def impute_ag(l, b, g, ag, faint_mag_min=11.0):
    """A_G 缺失的星用所在 1° 格（平滑后）的均值插补。"""
    li, bi = cell_indices(l, b)
    fin = (g >= faint_mag_min) & np.isfinite(ag)
    s = np.zeros((360, 180)); c = np.zeros((360, 180))
    np.add.at(s, (li[fin], bi[fin]), ag[fin])
    np.add.at(c, (li[fin], bi[fin]), 1)
    sm = gaussian_filter(np.where(c > 0, s / np.maximum(c, 1), 0), 1.5, mode=("wrap", "nearest"))
    w = gaussian_filter((c > 0).astype(float), 1.5, mode=("wrap", "nearest"))
    fill = sm / np.maximum(w, 1e-6)
    return np.where(np.isfinite(ag), ag, fill[li, bi])


def column_extinction_map(l, b, g, ag_eff, faint_mag_min=11.0, pct=90.0, min_stars=5):
    """每 1° 格 G>=faint_mag_min 星 A_G 的高分位数 ≈ 该方向全柱消光。"""
    li, bi = cell_indices(l, b)
    faint = g >= faint_mag_min
    cell = li[faint] * 180 + bi[faint]
    order = np.argsort(cell)
    cs, av = cell[order], ag_eff[faint][order]
    bounds = np.searchsorted(cs, np.arange(0, 360 * 180 + 1))
    acol = np.zeros((360, 180), np.float32)
    for cidx in range(360 * 180):
        s, e = bounds[cidx], bounds[cidx + 1]
        if e - s >= min_stars:
            acol[cidx // 180, cidx % 180] = np.percentile(av[s:e], pct)
    return gaussian_filter(acol, 1.0, mode=("wrap", "nearest"))


def build(out=OUT, faint_mag_min=11.0, pct=90.0):
    arrs = load_segments([os.path.join(RAW, s) for s in SEGMENTS])
    l, b, g, ag = arrs["l"], arrs["b"], arrs["g"], arrs["ag"]
    ag_eff = impute_ag(l, b, g, ag, faint_mag_min)
    acol = column_extinction_map(l, b, g, ag_eff, faint_mag_min, pct)
    li, bi = cell_indices(l, b)
    behind = np.clip(acol[li, bi] - ag_eff, 0.0, None)
    atten = (10.0 ** (-0.4 * behind)).astype(np.float32)
    np.savez(out, l=l, b=b, g=g,
             bp_rp=np.nan_to_num(arrs["bp_rp"], nan=0.7),
             proxy_atten=atten)
    faint = g >= faint_mag_min
    print(f"已保存 {out}: {len(g)} 颗星")
    print(f"暗星代理衰减 p10/50/90: {np.percentile(atten[faint], [10, 50, 90]).round(3)}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=OUT)
    p.add_argument("--faint-mag-min", type=float, default=11.0)
    p.add_argument("--acol-pct", type=float, default=90.0,
                   help="全柱消光取每格 A_G 的该分位数。")
    args = p.parse_args(argv)
    build(args.output, args.faint_mag_min, args.acol_pct)


if __name__ == "__main__":
    main()
