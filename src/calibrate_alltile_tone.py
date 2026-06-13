"""全天 tone 标定：测出全量渲染配置下的暗空 floor 锚（sky_anchor），冻结成全局常数喂给
每张 tile，复刻 hero 的对比观感且块间一致（无接缝）。

背景（接缝 vs 对比的矛盾，以及为什么标定的核心只是一个 anchor）：
  hero 单图（render_fov）好看，用的 tone 链和瓦片（render_tan_wcs）**完全相同**
  （adapt_sky_floor → finish_sky_adapted）。瓦片若让每张 tile 各自用 percentile 估 floor/
  white-point，含银带多的 tile 标定不同 → 沿银河方向接缝。解法：全天用一组固定的
  (sky_anchor, star_contrast, stretch) 喂所有 tile。

  star_contrast 和 stretch 是与 fov/size 无关的固定值（经验定：sc=4、stretch=1.0 复刻 hero
  的暗压亮提且银心不爆）。**真正需要标定的只有 sky_anchor**——它是「暗空区 canvas 的 sum
  底」，必须实测，原因见下。

为什么 sky_anchor 必须用「与全量渲染相同的 tile-fov/tile-size」实测（不能写死、不能用低分辨率）：
  render_tan_wcs 有一步立体角归一化 canvas *= REF_OMEGA/cdelt²（cdelt=tile_fov/tile_size）。
  它只乘星场 flux，不乘 add_skyglow 之后加的天光底。所以暗空区 canvas sum =
  暗星flux×norm + 天光底×3，其中 norm 随 fov/size 变（实测 fov6/512→3.84, fov20/650→4.11,
  fov6/2048→2.73）。因此 sky_anchor 依赖渲染配置，必须用相同 tile-fov/tile-size 渲一块暗空
  tile 实测其 sum 的 p25。这也修正了早先「size 不敏感」的错误假设——对 anchor 不成立。

  （注：hero render_fov 没有这步立体角归一化，所以 hero 的 tone 输入量级与 tile 差 ~norm 倍，
  hero 的 stretch 数值不能直接搬到 tile——这是早先复刻失败的真根因。）

用法（全量渲染前跑一次，用与全量相同的 tile-fov/tile-size）：
  python src/calibrate_alltile_tone.py --data data/raw/fov_g20_bsc5.npz \
      --tile-fov 6 --tile-size 2048 --value 6 --target-sky 0.020 \
      --star-contrast 4 --target-white 2.6 --out outputs/alltile_calib.json

  渲染时读 calib：render_tan_wcs 加 --calib outputs/alltile_calib.json。
"""
import argparse
import json

import numpy as np

import render_starmap as rs
import render_bortle_eye_grid as beg
import render_tan_wcs as rtw


# 高纬纯暗空标定点（远离银心/银带，canvas sum 落在天光底+暗星）。多点取中位更稳。
DARK_POINTS = [(74.0, 29.0), (60.0, 35.0), (-35.0, 33.0), (50.0, -28.0)]


def _dark_anchor(l, b, cols, L, tile_fov, tile_size, psf, bortle):
    """用与全量渲染相同的 fov/size，在几个高纬暗空点渲 raw canvas，取暗空 sum 的 p25 中位。"""
    cdelt = tile_fov / tile_size
    scale_rad = np.radians(cdelt)
    sat = 6.0 * beg.rh.skyglow_level(bortle) * beg.gain_for_mag_delta(0.0)
    p25s = []
    for lc, bc in DARK_POINTS:
        xi, eta, vis = rtw.gnomonic(l, b, lc, bc)
        px = tile_size / 2.0 + xi / scale_rad
        py = tile_size / 2.0 - eta / scale_rad
        inside = vis & (px >= 0) & (px < tile_size) & (py >= 0) & (py < tile_size)
        if inside.sum() == 0:
            continue
        pxi = np.clip(px.astype(int), 0, tile_size - 1)
        pyi = np.clip(py.astype(int), 0, tile_size - 1)
        c = rs.accumulate_stars(tile_size, tile_size, pxi, pyi, inside, L, cols, psf_px=psf)
        c = c * (rtw.REF_OMEGA / cdelt ** 2)
        c = beg.saturate_and_bloom(c, sat, (3.0, 9.0), (0.65, 0.35))
        c = beg.add_skyglow(c, bortle)
        p25s.append(float(np.percentile(c.sum(-1), 25.0)))
    if not p25s:
        raise SystemExit("没有暗空标定点落在数据范围内")
    return float(np.median(p25s)), p25s


def calibrate(data, tile_fov, tile_size, value, target_sky, star_contrast,
              target_white, stretch, psf, bortle):
    with np.load(data) as d:
        l, b, g = d["l"][:], d["b"][:], d["g"][:]
        bv = np.nan_to_num(d["bp_rp"][:], nan=0.7)
    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, bortle, value, 0.5)

    sky_anchor, p25s = _dark_anchor(l, b, cols, L, tile_fov, tile_size, psf, bortle)
    print(f"暗空 anchor 标定（fov={tile_fov} size={tile_size}）：各点 p25={['%.3f' % x for x in p25s]}"
          f" → 中位 {sky_anchor:.4f}", flush=True)

    return {
        "sky_anchor": float(sky_anchor),     # 喂 adapt_sky_floor 的 sky_anchor（依赖 fov/size！）
        "star_contrast": float(star_contrast),
        "stretch": float(stretch),
        "target_sky": float(target_sky),
        "target_white": float(target_white),
        "value": float(value),
        "bortle": int(bortle),
        "tile_fov": float(tile_fov),         # 记录标定时的 fov/size，渲染须一致
        "tile_size": int(tile_size),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--tile-fov", type=float, required=True, help="须与全量渲染的 --tile-fov 一致")
    ap.add_argument("--tile-size", type=int, required=True, help="须与全量渲染的 --tile-size 一致")
    ap.add_argument("--value", type=float, default=6.0)
    ap.add_argument("--target-sky", type=float, default=0.020)
    ap.add_argument("--star-contrast", type=float, default=4.0,
                    help="hero 同款对比；经验 sc=4 暗压亮提且银心不爆（sc=6 银心略过曝）")
    ap.add_argument("--target-white", type=float, default=2.6)
    ap.add_argument("--stretch", type=float, default=1.0,
                    help="全天固定 white-point stretch；归一化后亮部已足，1.0 即复刻 hero")
    ap.add_argument("--psf-core-px", type=float, default=0.6)
    ap.add_argument("--bortle", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    calib = calibrate(args.data, args.tile_fov, args.tile_size, args.value,
                      args.target_sky, args.star_contrast, args.target_white,
                      args.stretch, args.psf_core_px, args.bortle)
    with open(args.out, "w") as f:
        json.dump(calib, f, indent=2)
    print(f"\n=== 全天 tone 标定（hero 同款）===", flush=True)
    print(f"sky_anchor={calib['sky_anchor']:.4f}  star_contrast={calib['star_contrast']}  "
          f"stretch={calib['stretch']}", flush=True)
    print(f"→ {args.out}", flush=True)


if __name__ == "__main__":
    main()
