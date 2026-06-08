"""重拉 Gaia DR3 带 parallax(视差→距离)的子集, 用于 3D reproject。

视差倒数 = 距离(pc)。带上 ra/dec + parallax, 即可算每颗星的 3D 笛卡尔坐标,
把观测者从太阳系挪到任意点重投影。

只取 parallax 可靠的星(parallax>0 且 parallax/parallax_error 高), 否则距离是噪声。
为做"飞出去几十光年星座散架"演示, 近处星(视差大、距离准)才是主角。
"""
import os
import numpy as np

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep.npz")


def fetch(gmax=9.0, snr_min=5.0, out=None):
    """拉 G<gmax 且 parallax SNR>snr_min 的星。返回并缓存 ra/dec/parallax/g/bp_rp。"""
    from astroquery.gaia import Gaia
    # ADQL: 带 parallax 和误差, 过滤可靠视差
    q = f"""
    SELECT ra, dec, parallax, parallax_over_error, phot_g_mean_mag, bp_rp
    FROM gaiadr3.gaia_source
    WHERE phot_g_mean_mag < {gmax}
      AND parallax IS NOT NULL
      AND parallax > 0
      AND parallax_over_error > {snr_min}
    """
    print(f"querying Gaia DR3 G<{gmax}, parallax SNR>{snr_min}...")
    job = Gaia.launch_job_async(q)
    t = job.get_results()
    print(f"got {len(t)} stars")
    ra = np.asarray(t["ra"], float)
    dec = np.asarray(t["dec"], float)
    plx = np.asarray(t["parallax"], float)        # mas
    g = np.asarray(t["phot_g_mean_mag"], float)
    bp_rp = np.nan_to_num(np.asarray(t["bp_rp"], float), nan=0.7)
    dist_pc = 1000.0 / plx                          # mas→pc
    out = out or OUT
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, ra=ra, dec=dec, parallax=plx, dist_pc=dist_pc, g=g, bp_rp=bp_rp)
    print(f"saved {out}: {len(ra)} stars, dist range {dist_pc.min():.1f}-{dist_pc.max():.0f} pc")
    return ra, dec, dist_pc, g, bp_rp


if __name__ == "__main__":
    fetch()
