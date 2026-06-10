"""深星表裂隙 probing：缝/亮云的积分光通量比随星等深度怎么变。

核心问题：我们之前从 G<13 外推，推断"加深星表对压黑大裂隙收效甚微，缝的对比
受 Gaia DR3 深度物理锁死"。现在广州银心 FOV 的完整 Flatiron 深星表（到 ~21
等）已在本地，可以直接用真数据检验这个外推，而不是停在推断上。

方法：定两个 2° 圆窗——裂隙核心（暗带最黑处）和亮云对照（亮星云）。读覆盖这
两窗的 Flatiron 分片，对每个星等阈值 G<11/13/16/18/20，算两窗各自的积分光通
量和星数，输出 rift/cloud 光比。

判读：
  - 若光比随深度持续压低、逼近真实照片的几十倍对比（rift/cloud ~0.02-0.05），
    说明加深星表有用，外推错了，值得全量渲染。
  - 若光比随深度基本不动（卡在某个量级），说明外推对，是物理边界——缝里的光
    主力是消光压不没的前景星/较亮星，再深的暗星补不进缝的对比。

光比用积分光通量（不是星数）：渲染图的灰度来自光通量，缝发灰是前景星的光摊在
那儿。星数比会高估深暗星的贡献（暗星多但单颗光弱），光比才对应渲染里的对比。

用法：
  python src/probe_rift_depth.py
"""
import csv
import gzip
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD_DIR = os.path.join(ROOT, "data", "raw", "flatiron_gaia_source_fov_gz")
MANIFEST = os.path.join(SHARD_DIR, "manifest.csv")

# 两个测量窗（来自 probe_rift_windows.py 的实测定位）
RIFT = (1.50, 4.50)      # 裂隙核心 (l,b)，大裂隙暗带
CLOUD = (330.50, -2.50)  # 亮云对照 (l,b)，内盘星云
RADIUS_DEG = 2.0
GMAGS = [11.0, 13.0, 16.0, 18.0, 20.0]

# Flatiron ECSV 读取需要的最小列
USECOLS = ["l", "b", "phot_g_mean_mag"]


def angsep_deg(l1, b1, l2, b2):
    """球面大圆角距，银道度。"""
    l1, b1, l2, b2 = map(np.radians, (l1, b1, l2, b2))
    s = np.sin(b1) * np.sin(b2) + np.cos(b1) * np.cos(b2) * np.cos(l1 - l2)
    return np.degrees(np.arccos(np.clip(s, -1, 1)))


def window_to_healpix8(lc, bc, radius_deg, n_ring=8, n_rad=4):
    """采样圆盘（中心+同心环），银道→ICRS，返回覆盖的 nested HEALPix-8 index 集。"""
    from astropy_healpix import HEALPix
    from astropy.coordinates import SkyCoord, Galactic, ICRS
    import astropy.units as u

    hp = HEALPix(nside=256, order="nested", frame=ICRS())
    ls, bs = [lc], [bc]
    for rr in np.linspace(radius_deg / n_rad, radius_deg, n_rad):
        for th in np.linspace(0, 2 * np.pi, n_ring * 4, endpoint=False):
            b1 = np.radians(bc)
            d = np.radians(rr)
            b2 = np.arcsin(np.sin(b1) * np.cos(d)
                           + np.cos(b1) * np.sin(d) * np.cos(th))
            dl = np.arctan2(np.sin(th) * np.sin(d) * np.cos(b1),
                            np.cos(d) - np.sin(b1) * np.sin(b2))
            ls.append((lc + np.degrees(dl)) % 360.0)
            bs.append(np.degrees(b2))
    gal = SkyCoord(l=np.array(ls) * u.deg, b=np.array(bs) * u.deg, frame=Galactic)
    icrs = gal.icrs
    idx = hp.lonlat_to_healpix(icrs.ra, icrs.dec)
    return set(int(i) for i in np.unique(idx))


def load_manifest():
    rows = []
    with open(MANIFEST) as f:
        for r in csv.DictReader(f):
            rows.append({"name": r["name"], "lo": int(r["healpix8_min"]),
                         "hi": int(r["healpix8_max"]), "size": int(r["size_bytes"])})
    return rows


def shards_for_indices(indices, manifest):
    idx = np.array(sorted(indices))
    return [row for row in manifest
            if np.any((idx >= row["lo"]) & (idx <= row["hi"]))]


def _ecsv_header_line(path):
    """ECSV 开头约 1000 行 # 注释，找第一条非 # 行（CSV header）的行号。"""
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if not line.startswith("#"):
                return i
    return 0


def read_shard_window(path, lc, bc, radius_deg):
    """读一个分片，返回落在窗内的星的 (l, b, g) 数组。只取需要的列。"""
    skip = _ecsv_header_line(path)
    # ECSV 数据里 l,b,phot_g_mean_mag 都是数值列；缺测为空串→NaN
    df = pd.read_csv(path, skiprows=skip, usecols=USECOLS,
                     compression="gzip", low_memory=False)
    l = df["l"].to_numpy(dtype=float)
    b = df["b"].to_numpy(dtype=float)
    g = df["phot_g_mean_mag"].to_numpy(dtype=float)
    ok = np.isfinite(l) & np.isfinite(b) & np.isfinite(g)
    l, b, g = l[ok], b[ok], g[ok]
    sep = angsep_deg(l, b, lc, bc)
    inw = sep <= radius_deg
    return l[inw], b[inw], g[inw]


def collect_window(label, lc, bc, manifest):
    """收集一个窗口所有分片里的窗内星，返回该窗的 g 数组。"""
    idx = window_to_healpix8(lc, bc, RADIUS_DEG)
    shards = shards_for_indices(idx, manifest)
    print(f"[{label}] (l,b)=({lc},{bc}) r={RADIUS_DEG}°  "
          f"{len(shards)} 分片，{sum(s['size'] for s in shards)/1e9:.1f} GB")
    gs = []
    for i, s in enumerate(shards):
        path = os.path.join(SHARD_DIR, s["name"])
        _, _, g = read_shard_window(path, lc, bc, RADIUS_DEG)
        gs.append(g)
        print(f"  [{i+1}/{len(shards)}] {s['name']}  窗内 {g.size} 星", flush=True)
    return np.concatenate(gs) if gs else np.array([])


def flux(g):
    """Pogson：相对积分光通量 ∝ 10^(-0.4 G)。"""
    return np.power(10.0, -0.4 * g)


def main():
    manifest = load_manifest()
    print("=== 收集两窗的深星表（到 ~21 等全深度）===")
    g_rift = collect_window("RIFT", *RIFT, manifest)
    g_cloud = collect_window("CLOUD", *CLOUD, manifest)
    print(f"\nRIFT 窗内总星数（全深度）= {g_rift.size}")
    print(f"CLOUD 窗内总星数（全深度）= {g_cloud.size}")

    print("\n=== 光比随星等深度 ===")
    print(f"{'G<':>5} {'rift_N':>9} {'cloud_N':>9} {'N比':>7} "
          f"{'rift_flux':>11} {'cloud_flux':>11} {'光比(rift/cloud)':>16}")
    rows = []
    for gm in GMAGS:
        rm = g_rift[g_rift < gm]
        cm = g_cloud[g_cloud < gm]
        fr, fc = flux(rm).sum(), flux(cm).sum()
        nratio = rm.size / max(cm.size, 1)
        framio = fr / max(fc, 1e-30)
        rows.append((gm, rm.size, cm.size, nratio, fr, fc, framio))
        print(f"{gm:>5.0f} {rm.size:>9d} {cm.size:>9d} {nratio:>7.3f} "
              f"{fr:>11.3e} {fc:>11.3e} {framio:>16.4f}")

    print("\n=== 判读 ===")
    f11 = rows[0][6]
    f20 = rows[-1][6]
    print(f"光比 G<11 → G<20: {f11:.4f} → {f20:.4f}")
    if f20 > 0:
        print(f"加深 9 个星等，光比变化倍数: {f20/f11:.2f}×")
    print("真实照片的缝/云对比约 0.02-0.05（几十倍）。若 G<20 光比仍远高于此，"
          "说明加深无效、是物理边界；若显著趋近，说明加深有用、值得全量渲染。")


if __name__ == "__main__":
    main()
