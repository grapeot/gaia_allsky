"""从 3D 子集自己的光度函数推视频侧截断补偿增益(G<13 版)。

方法论同 rfc.md 的静态图推导:
  1. 拟合星表光度函数斜率 s = dlogN/dm (在亮端、计数完备的窗口内拟合, 避开
     截断处的滚降)。
  2. 每个 0.5 等 bin 的积分光通量 ∝ N(bin) × L(bin中心)。在 Pogson 标度下
     L ∝ 10^(-0.4·m), 而 N ∝ 10^(s·m), 所以单 bin 积分光 ∝ 10^((s-0.4)·m)。
  3. faint_mag_min 移到 11: 用 G in [11,13] 的暗星代理 G>13 不可分辨族群。
     缺失积分光 = ∫_{13}^{21} 外推光度函数的积分光 / [11,13] bin 的积分光。
     增益 = 这个比值(代理星扛起被截断族群的总积分光)。

注意 3D 子集不完备性: 视差质量筛选(parallax_over_error>5)在暗端通过率下降,
所以 [11,13] 实际通过的星数比全天少。增益按**实际通过的暗星**算——代理星少了,
每颗要扛的积分光就多, 所以这里直接用筛后星表拟合斜率, 不做完备性回补。
"""
import os
import numpy as np

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep_g13.npz")


def fit_slope(g, lo, hi):
    """在 [lo, hi) 用 0.2 等 bin 拟合 logN vs m 的斜率(最小二乘)。"""
    edges = np.arange(lo, hi + 1e-9, 0.2)
    centers = 0.5 * (edges[:-1] + edges[1:])
    counts = np.histogram(g, bins=edges)[0].astype(float)
    ok = counts > 0
    A = np.vstack([centers[ok], np.ones(ok.sum())]).T
    slope, intercept = np.linalg.lstsq(A, np.log10(counts[ok]), rcond=None)[0]
    return slope, intercept, centers, counts


def bin_integrated_light(N, m_center):
    """单 bin 积分光 ∝ N × L(m_center), L ∝ 10^(-0.4 m)。"""
    return N * 10.0 ** (-0.4 * m_center)


def main():
    d = np.load(DATA)
    g = d["g"]
    plx_snr = None
    print(f"3D G13 星表: N={len(g):,}  G {g.min():.3f}-{g.max():.3f}  median {np.median(g):.3f}")

    # 星等分布(0.5 等 bin)
    edges = np.arange(0, 13.5, 0.5)
    hist = np.histogram(g, bins=edges)[0]
    print("\n星等分布 (0.5 等 bin):")
    for i in range(len(hist)):
        if hist[i] > 0:
            print(f"  G [{edges[i]:>4.1f},{edges[i+1]:>4.1f}): {hist[i]:>9,}")

    # 拟合斜率: 在 G 8-12 的完备窗口拟合(避开 13 处滚降和亮端小数定)
    slope, intercept, centers, counts = fit_slope(g, 8.0, 12.0)
    print(f"\n光度函数斜率 dlogN/dm (G 8-12 拟合): s = {slope:.4f}")

    # 缺失积分光: 把 [13,21] 的 logN 外推, 算积分光; 与 [11,13] 代理 bin 比
    # 用 0.5 等 bin 离散积分
    proxy_lo, proxy_hi = 11.0, 13.0
    proxy_edges = np.arange(proxy_lo, proxy_hi + 1e-9, 0.5)
    proxy_centers = 0.5 * (proxy_edges[:-1] + proxy_edges[1:])
    proxy_N = np.histogram(g, bins=proxy_edges)[0].astype(float)
    proxy_light = bin_integrated_light(proxy_N, proxy_centers).sum()

    # 外推 [13,21]: N_bin = 10^(intercept + slope*m_center) * (bin宽/拟合bin宽比例)
    # 拟合用 0.2 等 bin, 这里用 0.5 等 bin, 需换算 bin 宽: N(0.5) = N(0.2)*(0.5/0.2)
    miss_edges = np.arange(13.0, 21.0 + 1e-9, 0.5)
    miss_centers = 0.5 * (miss_edges[:-1] + miss_edges[1:])
    N_per_02 = 10.0 ** (intercept + slope * miss_centers)
    N_per_05 = N_per_02 * (0.5 / 0.2)
    miss_light = bin_integrated_light(N_per_05, miss_centers).sum()

    ratio = miss_light / proxy_light
    print(f"\n代理 bin [11,13) 实际积分光(任意单位): {proxy_light:.4g}  (实际通过 {int(proxy_N.sum()):,} 颗)")
    print(f"外推 [13,21) 缺失积分光: {miss_light:.4g}")
    print(f"\n缺失/代理 = {ratio:.3f}  → 推荐 faint_gain ≈ {ratio:.2f} (faint_mag_min=11)")
    print("\n(对照: 全天静态图 G13 用 faint_mag_min=11 / faint_gain=3.8, 缺失≈2.8×)")


if __name__ == "__main__":
    main()
