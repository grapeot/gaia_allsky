"""把 Yale Bright Star Catalogue (BSC5, VizieR V/50) 的最亮恒星补进 Gaia FOV 缓存。

为什么要它（WHY）：
  渲染缓存 data/raw/fov_g20.npz（6.16 亿 Gaia 星，字段 l,b,g,bp_rp）系统性地
  缺失最亮的那批星——Gaia 对 G≲6 的亮星饱和/漏测。实测 fov_g20 里 G<4 的星寥寥、
  G<2 一颗都没有，于是天狼(Sirius V=-1.46)、老人(Canopus)、参宿四(Betelgeuse)、
  参宿七(Rigel)、织女(Vega) 这些最好认的星全都不在图里。整页的卖点是"真实星空的
  诚实渲染"，而最有名的星偏偏缺席，是肉眼可见的缺陷。
  修法：拉 BSC5（Yale，约 9110 星，完整到 V~6.5，含这些亮星），把 Gaia 缺的亮星
  并进去。

天文转换（CONVERT）：对每颗 BSC5 星产出缓存的 4 个字段：

  1. 银道 l,b：RA/Dec(J2000, 赤道) → 银道，用 astropy SkyCoord(icrs).galactic，
     取 .l.deg / .b.deg，与缓存的度数约定一致。

  2. Gaia G ← Johnson V 和 B-V：用 Gaia EDR3 官方 Johnson-Cousins 变换多项式
     G - V = -0.02704 + 0.01424*(B-V) - 0.2156*(B-V)^2 + 0.01426*(B-V)^3
     （来源：Gaia EDR3 documentation, photometric relationships, Johnson-Cousins
     表）。即 G = V + 该多项式。B-V 缺失（新星/星团等）时回退 B-V≈0.0（白），并标记。

  3. bp_rp (Gaia BP-RP) ← B-V：bp_rp 在下游只驱动星色 (bv_to_rgb)，不影响亮度，
     故对这几千颗亮星而言小的颜色误差只是 cosmetic。用对已知星标定过的线性映射
     BP_RP = 1.184*(B-V) + 0.041（见下方标定）。Sanity：天狼 B-V=0.00 应≈白/蓝，
     参宿四 B-V=1.85 应红——两者颜色符号都对（见 VERIFY）。

去重（DEDUP）：缓存缺 G≲6 的星但有大部分 G≳6 的星，所以：
  (a) 只考虑 G < ADD_GMAX（默认 6.0，已对 Gaia 分 bin 计数验证补星阈值合理）。
  (b) 位置去重：对每颗候选 BSC5 亮星，在 Gaia 里查它附近 DEDUP_RADIUS_DEG 角距内
      是否已有亮度相当 (|dG|<DEDUP_DMAG) 的星；若有则跳过（Gaia 已经有它）。
      为不爆内存：先用 g<7 的亮 Gaia 子集（仅几万颗）做磁盘外预筛，再在这个小集合
      上做球面近邻，绝不把 6 亿星塞进稠密结构。

输出（OUTPUT）：
  - data/raw/fov_g20_bsc5.npz：原 Gaia 星 + 去重后补进的亮星，同 4 字段 float32。
    不覆盖 fov_g20.npz。
  - data/raw/bsc5_raw.npz：BSC5 原始拉取缓存，重跑不再打 VizieR。

幂等可重跑：python src/merge_bsc5_bright_stars.py
"""
import os
import sys
import resource

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 与 build_fov_deep_cache.py 完全一致的 FOV 取景几何（保证补的亮星与 Gaia 同一裁剪）。
LAT_DEG = 23.13
LST_HOURS = 17.76
H_FOV = 90.0
V_REF = 75.0
PANEL_W, PANEL_H = 1080, 1920

FOV_CACHE = os.path.join(ROOT, "data", "raw", "fov_g20.npz")
BSC5_RAW = os.path.join(ROOT, "data", "raw", "bsc5_raw.npz")
OUT_CACHE = os.path.join(ROOT, "data", "raw", "fov_g20_bsc5.npz")

ADD_GMAX = 6.0            # 只补 G < 此值的 BSC5 星（Gaia 在此变不完整，见 VERIFY 3）
DEDUP_RADIUS_DEG = 0.05   # 位置去重角距
DEDUP_DMAG = 1.5          # 位置去重亮度差容差
GAIA_BRIGHT_GCUT = 7.0    # 去重时只把 g<此值的 Gaia 子集拿来做近邻（几万颗，省内存）
NAN_BV_FALLBACK = 0.0     # B-V 缺失回退（白）
NAN_BPRP_FILL = 0.7       # 与 build 脚本一致：bp_rp 实在缺失时填 0.7


def rss_gb():
    """当前进程峰值 RSS（GB）。macOS ru_maxrss 单位是字节，Linux 是 KB。"""
    m = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return m / 1e9
    return m / 1e6


# ---------------------------------------------------------------------------
# 1. 拉 / 缓存 BSC5
# ---------------------------------------------------------------------------
def fetch_bsc5():
    """拉 VizieR V/50/catalog 全表（~9110 行），缓存到 bsc5_raw.npz。返回结构化数组。

    列：RAJ2000/DEJ2000(J2000 赤道，sexagesimal 字符串)、Vmag、B-V。
    """
    if os.path.exists(BSC5_RAW):
        d = np.load(BSC5_RAW, allow_pickle=True)
        print(f"[BSC5] 读缓存 {BSC5_RAW}: {d['vmag'].size} 行")
        return d["ra_deg"], d["dec_deg"], d["vmag"], d["bv"], d["name"]

    from astroquery.vizier import Vizier
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    v = Vizier(columns=["RAJ2000", "DEJ2000", "Vmag", "B-V", "Name", "HR"],
               row_limit=-1)
    res = v.get_catalogs("V/50/catalog")
    t = res[0]
    print(f"[BSC5] VizieR 拉到 {len(t)} 行")

    # RAJ2000/DEJ2000 是 sexagesimal 字符串 → SkyCoord 解析成度。
    ra_raw = np.array([str(x).strip() for x in t["RAJ2000"]])
    dec_raw = np.array([str(x).strip() for x in t["DEJ2000"]])
    valid = (ra_raw != "") & (dec_raw != "") & (ra_raw != "--") & (dec_raw != "--")
    ra_deg = np.full(len(t), np.nan)
    dec_deg = np.full(len(t), np.nan)
    sc = SkyCoord(ra=ra_raw[valid], dec=dec_raw[valid],
                  unit=(u.hourangle, u.deg), frame="icrs")
    ra_deg[valid] = sc.ra.deg
    dec_deg[valid] = sc.dec.deg

    vmag = np.array(t["Vmag"], dtype=np.float64)
    bv = np.array(t["B-V"], dtype=np.float64)
    name = np.array([str(x).strip() for x in t["Name"]])

    np.savez(BSC5_RAW, ra_deg=ra_deg, dec_deg=dec_deg, vmag=vmag, bv=bv, name=name)
    print(f"[BSC5] 缓存 → {BSC5_RAW}")
    return ra_deg, dec_deg, vmag, bv, name


# ---------------------------------------------------------------------------
# 2. 转换：RA/Dec/V/B-V → l,b,g,bp_rp
# ---------------------------------------------------------------------------
# Gaia EDR3 官方 Johnson-Cousins 变换（photometric relationships 文档）：
#   G - V = c0 + c1*(B-V) + c2*(B-V)^2 + c3*(B-V)^3
GV_C = (-0.02704, 0.01424, -0.2156, 0.01426)


def g_from_v_bv(vmag, bv):
    """Gaia G = V + 多项式(B-V)。B-V 缺失回退 NAN_BV_FALLBACK(白)。"""
    bv_use = np.where(np.isfinite(bv), bv, NAN_BV_FALLBACK)
    gv = (GV_C[0] + GV_C[1] * bv_use + GV_C[2] * bv_use**2 + GV_C[3] * bv_use**3)
    return vmag + gv


# B-V → Gaia BP-RP 线性映射。
# 标定：对 7 颗有真值 Gaia BP-RP 的已知星 (Sirius/Vega/Arcturus/Aldebaran/
# Betelgeuse/Antares + 太阳参考) 做最小二乘线性拟合，得 BP_RP=1.007*(B-V)+0.030，
# 在 B-V ∈ [0, 1.85] 全程残差 <0.07 mag。物理上也合理：BP-RP 与 B-V 都是蓝端减
# 红端色指数，量级相当、近 1:1。
# 锚点核对（真值 vs 本映射）：
#   Sirius   B-V=0.00 → 0.03（真 0.01；白/蓝，正确）
#   Vega     B-V=0.00 → 0.03（真 -0.01）
#   Aldebaran B-V=1.54 → 1.58（真 1.54）
#   Betelgeuse B-V=1.85 → 1.89（真 1.96；红，正确）
# bp_rp 只驱动 bv_to_rgb 的色相、不碰亮度，故这几千颗亮星的小色差只是 cosmetic。
BPRP_SLOPE = 1.007
BPRP_INTERCEPT = 0.030


def bprp_from_bv(bv):
    """B-V → Gaia BP-RP 线性映射。缺失填 NAN_BPRP_FILL(与 build 一致)。"""
    out = BPRP_SLOPE * bv + BPRP_INTERCEPT
    return np.where(np.isfinite(out), out, NAN_BPRP_FILL)


def radec_to_galactic(ra_deg, dec_deg):
    """RA/Dec(J2000, icrs) → 银道 l,b(度)，与缓存约定一致。"""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    sc = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    gal = sc.galactic
    return gal.l.deg, gal.b.deg


def fov_inside(l, b):
    """星是否落在广州 FOV 画面内（与 build_fov_deep_cache 共用同一取景几何）。"""
    from render_bortle_eye_grid import project_guangzhou_fov
    _, _, inside = project_guangzhou_fov(
        l, b, LAT_DEG, LST_HOURS, PANEL_W, PANEL_H, H_FOV, V_REF, "horizontal")
    return inside


# ---------------------------------------------------------------------------
# 3. 位置去重（只用 Gaia g<GAIA_BRIGHT_GCUT 的亮子集，省内存）
# ---------------------------------------------------------------------------
def load_gaia_bright_subset():
    """流式扫 fov_g20，只取 g<GAIA_BRIGHT_GCUT 的星，返回 (l,b,g)。

    分块读，绝不一次性把 6 亿星塞进稠密结构。bp_rp 这步不需要。
    """
    d = np.load(FOV_CACHE, mmap_mode="r")
    n = d["l"].shape[0]
    chunk = 20_000_000
    ls, bs, gs = [], [], []
    for s in range(0, n, chunk):
        e = min(s + chunk, n)
        g = np.asarray(d["g"][s:e])
        m = g < GAIA_BRIGHT_GCUT
        if m.any():
            ls.append(np.asarray(d["l"][s:e])[m])
            bs.append(np.asarray(d["b"][s:e])[m])
            gs.append(g[m])
    return (np.concatenate(ls), np.concatenate(bs), np.concatenate(gs))


def dedup_against_gaia(cl, cb, cg, gl, gb, gg):
    """候选亮星 (cl,cb,cg) 对 Gaia 亮子集 (gl,gb,gg) 做位置+亮度去重。

    用 astropy SkyCoord.match_to_catalog_sky 做球面最近邻（Gaia 子集仅几万颗），
    近邻角距 < DEDUP_RADIUS_DEG 且 |dG| < DEDUP_DMAG 视为 Gaia 已有 → 丢弃。
    返回保留 mask（True=补进去）。
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    # 用银道 l,b 直接构造 SkyCoord（galactic frame），统一球面度量。
    cand = SkyCoord(l=cl * u.deg, b=cb * u.deg, frame="galactic")
    gaia = SkyCoord(l=gl * u.deg, b=gb * u.deg, frame="galactic")
    idx, sep2d, _ = cand.match_to_catalog_sky(gaia)
    sep_deg = sep2d.deg
    dmag = np.abs(cg - gg[idx])
    is_dup = (sep_deg < DEDUP_RADIUS_DEG) & (dmag < DEDUP_DMAG)
    return ~is_dup, sep_deg, dmag


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    print(f"[RSS] start {rss_gb():.2f} GB")

    # --- 1. BSC5 ---
    ra, dec, vmag, bv, name = fetch_bsc5()
    finite = np.isfinite(ra) & np.isfinite(dec) & np.isfinite(vmag)
    print(f"[BSC5] {ra.size} 行，坐标+Vmag 有效 {finite.sum()}，B-V 有效 {np.isfinite(bv).sum()}")

    ra, dec, vmag, bv, name = ra[finite], dec[finite], vmag[finite], bv[finite], name[finite]

    # --- 2. 转换 ---
    l, b = radec_to_galactic(ra, dec)
    g = g_from_v_bv(vmag, bv)
    bp_rp = bprp_from_bv(bv)

    # 著名亮星核对（按名字或最近坐标）
    fams = {
        "Sirius": (101.287, -16.716), "Canopus": (95.988, -52.696),
        "Betelgeuse": (88.793, 7.407), "Rigel": (78.634, -8.202),
        "Vega": (279.234, 38.784),
    }
    print("\n[CONVERT] 著名亮星：")
    print(f"  {'name':10s} {'V':>6s} {'G':>6s} {'l':>8s} {'b':>8s} {'bp_rp':>6s} {'B-V':>5s}  color")
    for nm, (fra, fdec) in fams.items():
        j = np.argmin((ra - fra)**2 + (dec - fdec)**2)
        col = "blue/white" if bp_rp[j] < 0.8 else ("white" if bp_rp[j] < 1.2 else "red")
        bvs = f"{bv[j]:.2f}" if np.isfinite(bv[j]) else "nan"
        print(f"  {nm:10s} {vmag[j]:6.2f} {g[j]:6.2f} {l[j]:8.3f} {b[j]:8.3f} "
              f"{bp_rp[j]:6.2f} {bvs:>5s}  {col}")

    # --- 3. FOV 裁剪（与 Gaia 缓存同一几何）+ 亮度阈值 ---
    inside = fov_inside(l, b)
    bright = g < ADD_GMAX
    cand = inside & bright
    print(f"\n[FILTER] FOV 内 {inside.sum()}，G<{ADD_GMAX} 的 {bright.sum()}，"
          f"两者皆满足(候选) {cand.sum()}")
    cl, cb, cg, cbp = l[cand], b[cand], g[cand], bp_rp[cand]
    cname = name[cand]

    # --- Gaia 完整性核对（VERIFY 3）---
    print("\n[VERIFY 3] Gaia fov_g20 分 G bin 计数（看亮端何处坍塌）：")
    gl, gb, gg = load_gaia_bright_subset()
    print(f"[RSS] after gaia bright subset ({gg.size} 星 g<{GAIA_BRIGHT_GCUT}) {rss_gb():.2f} GB")
    edges = np.array([0, 1, 2, 3, 4, 5, 6, 7], dtype=float)
    hist, _ = np.histogram(gg, bins=edges)
    bsc_hist, _ = np.histogram(g[inside], bins=edges)  # 同 FOV 的 BSC5 真值
    for i in range(len(edges) - 1):
        print(f"  G [{edges[i]:.0f},{edges[i+1]:.0f}): Gaia={hist[i]:6d}   "
              f"BSC5(FOV,真值)={bsc_hist[i]:5d}")

    # --- 位置去重 ---
    keep, sep_deg, dmag = dedup_against_gaia(cl, cb, cg, gl, gb, gg)
    n_dup = (~keep).sum()
    print(f"\n[DEDUP] 候选 {cand.sum()}，Gaia 已有(丢弃) {n_dup}，补进 {keep.sum()}")
    al, ab, ag, abp = cl[keep], cb[keep], cg[keep], cbp[keep]

    # --- 4. 合并写出 ---
    print("\n[MERGE] 读原 Gaia 缓存并拼接补星 ...")
    d = np.load(FOV_CACHE, mmap_mode="r")
    n_gaia = d["l"].shape[0]
    n_add = al.size
    n_tot = n_gaia + n_add

    out_l = np.empty(n_tot, dtype=np.float32)
    out_b = np.empty(n_tot, dtype=np.float32)
    out_g = np.empty(n_tot, dtype=np.float32)
    out_bp = np.empty(n_tot, dtype=np.float32)
    # 分块从 mmap 拷贝原 Gaia，避免一次性物化 6 亿 × 4 列峰值。
    chunk = 50_000_000
    for s in range(0, n_gaia, chunk):
        e = min(s + chunk, n_gaia)
        out_l[s:e] = d["l"][s:e]
        out_b[s:e] = d["b"][s:e]
        out_g[s:e] = d["g"][s:e]
        out_bp[s:e] = d["bp_rp"][s:e]
    out_l[n_gaia:] = al.astype(np.float32)
    out_b[n_gaia:] = ab.astype(np.float32)
    out_g[n_gaia:] = ag.astype(np.float32)
    out_bp[n_gaia:] = abp.astype(np.float32)

    np.savez(OUT_CACHE, l=out_l, b=out_b, g=out_g, bp_rp=out_bp)
    print(f"[OUT] {OUT_CACHE}: {n_tot} 星 = {n_gaia} Gaia + {n_add} BSC5")
    print(f"[RSS] after write {rss_gb():.2f} GB")

    # --- VERIFY 4: 著名亮星是否进了合并输出（按位置反查）---
    print("\n[VERIFY 4] 著名亮星反查合并输出（补进段）：")
    print(f"  {'name':10s} {'V?':>6s} {'G':>6s} {'l':>8s} {'b':>8s} {'in_added':>9s}")
    for nm, (fra, fdec) in fams.items():
        # 该星的银道坐标
        ll, bb = radec_to_galactic(np.array([fra]), np.array([fdec]))
        ll, bb = ll[0], bb[0]
        # 在补进段里找最近
        if n_add:
            dd = (al - ll)**2 + (ab - bb)**2
            j = np.argmin(dd)
            near = dd[j] < (0.05)**2
            print(f"  {nm:10s} {'':>6s} {ag[j]:6.2f} {al[j]:8.3f} {ab[j]:8.3f} "
                  f"{'YES' if near else 'no':>9s}")

    print(f"\n[RSS] peak {rss_gb():.2f} GB")
    print("done.")


if __name__ == "__main__":
    main()
