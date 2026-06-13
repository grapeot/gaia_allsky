"""把星表按 HEALPix 像素分桶排序，建索引，供 render_tan_wcs 每个 tile 只读邻桶的星
（memory-aware 根治：从「每 tile 扫全 6 亿星算投影、~30GB/进程」降到「只读覆盖的几个桶、
几十万星、几十 MB」，且快几百倍，让高分辨率全天可行）。

产出 NPZ（与输入同字段，但按 Norder6 nested HEALPix 像素排序）+ 桶索引：
  l, b, g, bp_rp   —— 按 hpx 像素排序后的星（float32）
  bucket_start     —— 每个 hpx 像素在数组里的起始下标（int64, 长 npix=12*4^order）
  bucket_count     —— 每个 hpx 像素的星数（int64, 长 npix）
  order            —— HEALPix Norder（标量）
取像素 p 的星：arr[bucket_start[p] : bucket_start[p]+bucket_count[p]]。

为什么 Norder6（55 arcmin/桶）：tile fov~0.6° 时每 tile 覆盖 ~几个桶；桶太小(N7/8)邻桶
多、索引大，桶太大(N5)单桶星多。Norder6 平均 ~1.25 万星/桶，银心桶几万，内存可控。

用法：
  python src/build_healpix_bucketed.py --in data/raw/fov_g20_bsc5.npz \
      --out data/raw/fov_g20_bsc5_hpx6.npz --order 6
"""
import argparse

import numpy as np
from astropy_healpix import HEALPix
from astropy.coordinates import Galactic
import astropy.units as u


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--order", type=int, default=6, help="HEALPix Norder（默认 6=55arcmin/桶）")
    args = ap.parse_args()

    with np.load(args.inp) as d:
        l = d["l"][:].astype(np.float64)
        b = d["b"][:].astype(np.float64)
        g = d["g"][:]
        bp_rp = d["bp_rp"][:]
    n = len(l)
    print(f"输入 {n:,} 星，算 Norder{args.order} nested healpix...", flush=True)

    nside = 2 ** args.order
    npix = 12 * nside * nside
    hp = HEALPix(nside=nside, order="nested", frame=Galactic())
    # 银道 l/b → healpix 像素（分块算，避免一次性物化太多中间）
    pix = np.empty(n, dtype=np.int64)
    CHUNK = 50_000_000
    for i in range(0, n, CHUNK):
        sl = slice(i, min(i + CHUNK, n))
        pix[sl] = hp.lonlat_to_healpix(l[sl] * u.deg, b[sl] * u.deg)
        print(f"  hpx {sl.stop:,}/{n:,}", flush=True)

    print("argsort 按像素排序...", flush=True)
    order_idx = np.argsort(pix, kind="stable")
    pix_sorted = pix[order_idx]
    l, b, g, bp_rp = l[order_idx], b[order_idx], g[order_idx], bp_rp[order_idx]
    del pix, order_idx

    # 桶索引：每个像素的 start/count（用 bincount + cumsum）
    bucket_count = np.bincount(pix_sorted, minlength=npix).astype(np.int64)
    bucket_start = np.zeros(npix, dtype=np.int64)
    bucket_start[1:] = np.cumsum(bucket_count)[:-1]
    del pix_sorted

    nonempty = int((bucket_count > 0).sum())
    print(f"分桶完成：{npix:,} 桶，{nonempty:,} 非空，"
          f"最大桶 {bucket_count.max():,} 星（银心），中位非空桶 "
          f"{int(np.median(bucket_count[bucket_count>0]))} 星", flush=True)

    np.savez(args.out,
             l=l.astype(np.float32), b=b.astype(np.float32),
             g=g.astype(np.float32), bp_rp=bp_rp.astype(np.float32),
             bucket_start=bucket_start, bucket_count=bucket_count,
             order=np.int32(args.order))
    print(f"已保存 {args.out}", flush=True)


if __name__ == "__main__":
    main()
