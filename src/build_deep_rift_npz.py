"""从 Flatiron 深星表分片 filter 出裂隙天区的 G<N 渲染缓存，用于真渲 G<20
银河、与 G<13 直接对比缝有没有变。

只读 probing 已定位的裂隙+亮云覆盖分片（47 片，~10GB），取一个覆盖两窗的矩
形天区，按给定星等阈值 filter，存成渲染器能吃的 NPZ（字段 l,b,g,bp_rp）。

用法：
  python src/build_deep_rift_npz.py --gmax 13 --out data/raw/rift_region_g13.npz
  python src/build_deep_rift_npz.py --gmax 20 --out data/raw/rift_region_g20.npz
"""
import argparse
import gzip
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD_DIR = os.path.join(ROOT, "data", "raw", "flatiron_gaia_source_fov_gz")

import sys
sys.path.insert(0, os.path.join(ROOT, "src"))
from probe_rift_depth import (RIFT, CLOUD, RADIUS_DEG, window_to_healpix8,
                              shards_for_indices, load_manifest, _ecsv_header_line)

# 覆盖裂隙 (1.5,4.5) + 亮云 (330.5,-2.5) 的矩形天区，l 绕 0 点
L_LO, L_HI = 325.0, 10.0    # 绕 0：l>=325 或 l<=10
B_LO, B_HI = -8.0, 10.0
USECOLS = ["l", "b", "phot_g_mean_mag", "bp_rp"]


def in_region(l, b):
    lin = (l >= L_LO) | (l <= L_HI)
    bin_ = (b >= B_LO) & (b <= B_HI)
    return lin & bin_


def read_shard_region(path, gmax):
    skip = _ecsv_header_line(path)
    df = pd.read_csv(path, skiprows=skip, usecols=USECOLS,
                     compression="gzip", low_memory=False)
    l = df["l"].to_numpy(dtype=float)
    b = df["b"].to_numpy(dtype=float)
    g = df["phot_g_mean_mag"].to_numpy(dtype=float)
    bp_rp = df["bp_rp"].to_numpy(dtype=float)
    ok = np.isfinite(l) & np.isfinite(b) & np.isfinite(g)
    l, b, g, bp_rp = l[ok], b[ok], g[ok], bp_rp[ok]
    keep = in_region(l, b) & (g < gmax)
    return l[keep], b[keep], g[keep], bp_rp[keep]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gmax", type=float, required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    manifest = load_manifest()
    # 裂隙 + 亮云两窗覆盖的分片并集
    idx = window_to_healpix8(*RIFT, RADIUS_DEG) | window_to_healpix8(*CLOUD, RADIUS_DEG)
    shards = shards_for_indices(idx, manifest)
    print(f"天区 l∈[{L_LO},{L_HI}](绕0) b∈[{B_LO},{B_HI}], G<{args.gmax}, "
          f"{len(shards)} 分片")

    ls, bs, gs, cs = [], [], [], []
    for i, s in enumerate(shards):
        path = os.path.join(SHARD_DIR, s["name"])
        l, b, g, bp_rp = read_shard_region(path, args.gmax)
        ls.append(l); bs.append(b); gs.append(g); cs.append(bp_rp)
        print(f"  [{i+1}/{len(shards)}] {s['name']}  +{l.size}", flush=True)

    l = np.concatenate(ls); b = np.concatenate(bs)
    g = np.concatenate(gs); bp_rp = np.concatenate(cs)
    # 去重：两窗分片并集可能有 HEALPix 边界重叠，按 (l,b,g) 唯一化
    key = np.stack([l, b, g], axis=1)
    _, uniq = np.unique(key, axis=0, return_index=True)
    l, b, g, bp_rp = l[uniq], b[uniq], g[uniq], bp_rp[uniq]
    bp_rp = np.nan_to_num(bp_rp, nan=0.7)

    np.savez(args.out, l=l, b=b, g=g, bp_rp=bp_rp)
    print(f"\n保存 {args.out}: {l.size} 星 (G<{args.gmax})")
    print(f"  G min/max: {g.min():.2f}/{g.max():.2f}")


if __name__ == "__main__":
    main()
