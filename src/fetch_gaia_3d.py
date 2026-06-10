"""重拉 Gaia DR3 带 parallax(视差→距离)的子集, 用于 3D reproject。

视差倒数 = 距离(pc)。带上 ra/dec + parallax, 即可算每颗星的 3D 笛卡尔坐标,
把观测者从太阳系挪到任意点重投影。

只取 parallax 可靠的星(parallax>0 且 parallax/parallax_error 高), 否则距离是噪声。
为做"飞出去几十光年星座散架"演示, 近处星(视差大、距离准)才是主角。

Gaia 档案库对匿名查询有 300 万行硬上限, 超限会被无声截断。取暗星段
(如 G<13, 全天 700 万行量级)时必须按星等分段查询再拼接(每段估算 <300 万行)。
3D 子集额外有视差质量筛选(parallax_over_error>snr_min), 暗星端通过率会下降,
所以每段实际行数比全天表更少, 但分段逻辑一致。
"""
import argparse
import os

import numpy as np

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep.npz")

# G<13 分段缓存的默认输出与默认分段边界。各段在全天表上 <200 万行,
# 3D 子集加视差筛选后只会更少, 安全避开 300 万行匿名截断上限。
OUT_G13 = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep_g13.npz")
DEFAULT_SEGMENTS_G13 = [
    (None, 11.0),     # G<11 (原 gaia_3d_deep 范围, 一次取完)
    (11.0, 12.0),
    (12.0, 12.5),
    (12.5, 12.8),
    (12.8, 13.0),
]

ROW_HARD_LIMIT = 3_000_000


def build_query(gmin, gmax, snr_min=5.0):
    """构造一个星等段的 ADQL 查询。gmin=None 表示不设下限(取最亮段)。"""
    gmin_clause = f"\n      AND phot_g_mean_mag >= {float(gmin)}" if gmin is not None else ""
    return f"""
    SELECT ra, dec, parallax, parallax_over_error, phot_g_mean_mag, bp_rp
    FROM gaiadr3.gaia_source
    WHERE phot_g_mean_mag < {float(gmax)}{gmin_clause}
      AND parallax IS NOT NULL
      AND parallax > 0
      AND parallax_over_error > {snr_min}
    """.strip()


def _table_to_arrays(t):
    ra = np.asarray(t["ra"], float)
    dec = np.asarray(t["dec"], float)
    plx = np.asarray(t["parallax"], float)        # mas
    g = np.asarray(t["phot_g_mean_mag"], float)
    bp_rp = np.nan_to_num(np.asarray(t["bp_rp"], float), nan=0.7)
    return ra, dec, plx, g, bp_rp


def fetch_segment(gmin, gmax, snr_min=5.0):
    """拉单个星等段 [gmin, gmax) 且 parallax SNR>snr_min 的星, 返回数组元组。

    会断言行数 != 300 万行硬上限, 防止无声截断混进缓存。
    """
    from astroquery.gaia import Gaia

    q = build_query(gmin, gmax, snr_min)
    rng = f"{gmin}<=G<{gmax}" if gmin is not None else f"G<{gmax}"
    print(f"querying Gaia DR3 {rng}, parallax SNR>{snr_min}...")
    job = Gaia.launch_job_async(q)
    t = job.get_results()
    n = len(t)
    print(f"  got {n} stars")
    if n == ROW_HARD_LIMIT:
        raise RuntimeError(
            f"段 {rng} 返回行数恰好 = {ROW_HARD_LIMIT} (匿名查询硬上限), "
            f"几乎肯定被无声截断, 请把这一段再细分。"
        )
    return _table_to_arrays(t)


def fetch(gmax=9.0, snr_min=5.0, out=None):
    """单段拉 G<gmax(无下限)的星并缓存。保留旧签名供既有调用。"""
    ra, dec, plx, g, bp_rp = fetch_segment(None, gmax, snr_min)
    dist_pc = 1000.0 / plx
    out = out or OUT
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, ra=ra, dec=dec, parallax=plx, dist_pc=dist_pc, g=g, bp_rp=bp_rp)
    print(f"saved {out}: {len(ra)} stars, dist range {dist_pc.min():.1f}-{dist_pc.max():.0f} pc")
    return ra, dec, dist_pc, g, bp_rp


def fetch_segmented(segments=None, snr_min=5.0, out=None):
    """按星等段分别查询再拼接, 缓存合并结果。返回合并后的数组。

    segments: [(gmin, gmax), ...] 列表, gmin=None 表示该段无下限。
    每段独立查询, 各自校验 != 300 万行硬上限, 然后 concatenate。
    """
    segments = segments or DEFAULT_SEGMENTS_G13
    out = out or OUT_G13
    chunks = {"ra": [], "dec": [], "plx": [], "g": [], "bp_rp": []}
    seg_counts = []
    for gmin, gmax in segments:
        ra, dec, plx, g, bp_rp = fetch_segment(gmin, gmax, snr_min)
        chunks["ra"].append(ra)
        chunks["dec"].append(dec)
        chunks["plx"].append(plx)
        chunks["g"].append(g)
        chunks["bp_rp"].append(bp_rp)
        seg_counts.append((gmin, gmax, len(ra)))

    ra = np.concatenate(chunks["ra"])
    dec = np.concatenate(chunks["dec"])
    plx = np.concatenate(chunks["plx"])
    g = np.concatenate(chunks["g"])
    bp_rp = np.concatenate(chunks["bp_rp"])
    dist_pc = 1000.0 / plx

    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, ra=ra, dec=dec, parallax=plx, dist_pc=dist_pc, g=g, bp_rp=bp_rp)
    print("---- 分段汇总 ----")
    for gmin, gmax, n in seg_counts:
        rng = f"{gmin}<=G<{gmax}" if gmin is not None else f"G<{gmax}"
        print(f"  {rng:>14}: {n:>9,} 颗")
    print(f"saved {out}: {len(ra):,} stars total, "
          f"G {g.min():.3f}-{g.max():.3f}, dist {dist_pc.min():.1f}-{dist_pc.max():.0f} pc")
    return ra, dec, dist_pc, g, bp_rp


def build_parser():
    p = argparse.ArgumentParser(description="获取带视差的 Gaia DR3 3D 子集缓存。")
    p.add_argument("--gmax", type=float, default=9.0, help="单段模式 G 星等上限。")
    p.add_argument("--snr-min", type=float, default=5.0, help="parallax_over_error 下限。")
    p.add_argument("--segmented", action="store_true",
                   help="按星等分段取 G<13 全集再拼接(避开 300 万行匿名截断)。")
    p.add_argument("--output", default=None, help="输出 NPZ 路径(默认: 单段 gaia_3d_deep, 分段 gaia_3d_deep_g13)。")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.segmented:
        return fetch_segmented(snr_min=args.snr_min, out=args.output)
    return fetch(gmax=args.gmax, snr_min=args.snr_min, out=args.output)


if __name__ == "__main__":
    main()
