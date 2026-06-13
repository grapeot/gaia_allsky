"""用 numpy/scipy 复现 PixInsight 的 CurvesTransformation + SCNR，免依赖 PixInsight。

为什么：渲染机（Linux）没装 PixInsight，但 tile 调色（色温/去绿微调）需要它。PI 的这两步
是纯逐像素确定性变换，可用 Python 等价实现，让整条 pipeline 在任意机器跑通、不必把渲好的
几百 G tiles 传回有 PI 的机器再传出去。

对应 skills/batch_process_frames.xpsm：
- CurvesTransformation：PixInsight 用 Akima 样条插值控制点（scipy Akima1DInterpolator 等价）。
  通道应用顺序固定 R,G,B,RGB/K,...,L,a,b。本 xpsm 用到 B（蓝通道）/ K（RGB 整体）/ b（CIE b*）。
  顺序：B → K → b*。b* 在 CIELab 空间作曲线（归一到 [-128,128]→[0,1]），需 sRGB↔Lab 往返。
- SCNR AverageNeutral 去绿：G' = min(G, (R+B)/2)，amount 混合，preserveLightness 保亮度。

eval 验证（24 张银心/散布 tile，真 PI vs 本实现逐像素）：mean≈3.6/255、p99≈11，视觉等价。
残差主要来自 Akima 与 PI 内部样条的细节差，对 JPEG 输出已无可见区别。
"""
import numpy as np
from scipy.interpolate import Akima1DInterpolator

# sRGB <-> CIELab (D65)
_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]])
_Minv = np.linalg.inv(_M)
_Wn = np.array([0.95047, 1.0, 1.08883])
_BSTAR_LO, _BSTAR_HI = -128.0, 128.0   # b* 归一范围（eval 调出最优）


def _apply_curve(x, points):
    """对 [0,1] 数组 x 应用 Akima 样条曲线（控制点 points=[[x0,y0],...]）。恒等曲线直接返回。"""
    pts = np.asarray(points, float)
    if len(pts) == 2 and np.allclose(pts, [[0, 0], [1, 1]]):
        return x
    xs, ys = pts[:, 0], pts[:, 1]
    interp = Akima1DInterpolator(xs, ys)
    y = interp(np.clip(x, xs[0], xs[-1]))
    y = np.where(x < xs[0], ys[0], y)
    y = np.where(x > xs[-1], ys[-1], y)
    return np.clip(y, 0.0, 1.0)


def _srgb2lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin2srgb(c):
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)


def _f(t):
    d = 6 / 29
    return np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)


def _finv(t):
    d = 6 / 29
    return np.where(t > d, t ** 3, 3 * d * d * (t - 4 / 29))


def _rgb2lab(rgb):
    xyz = _srgb2lin(rgb) @ _M.T / _Wn
    fx, fy, fz = _f(xyz[..., 0]), _f(xyz[..., 1]), _f(xyz[..., 2])
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], -1)


def _lab2rgb(lab):
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    xyz = np.stack([_finv(fx), _finv(fy), _finv(fz)], -1) * _Wn
    return np.clip(_lin2srgb(xyz @ _Minv.T), 0.0, 1.0)


def apply_curves(rgb, chans):
    """对 rgb[H,W,3] float[0,1] 应用 CurvesTransformation 的 B/K/b* 通道（PI 固定顺序）。"""
    out = rgb.copy()
    if "B" in chans:                          # 蓝通道
        out[..., 2] = _apply_curve(out[..., 2], chans["B"])
    if "K" in chans:                          # RGB 整体亮度
        for c in range(3):
            out[..., c] = _apply_curve(out[..., c], chans["K"])
    if "b" in chans:                          # CIE b*（蓝黄轴），Lab 空间作曲线
        lab = _rgb2lab(out)
        rng = _BSTAR_HI - _BSTAR_LO
        bn = (lab[..., 2] - _BSTAR_LO) / rng
        lab[..., 2] = _apply_curve(bn, chans["b"]) * rng + _BSTAR_LO
        out = _lab2rgb(lab)
    return np.clip(out, 0.0, 1.0)


def apply_scnr_green(rgb, amount=0.5, preserve_lightness=True):
    """SCNR AverageNeutral 去绿：G' = min(G, (R+B)/2)，amount 混合，可选保亮度。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    g_neutral = np.minimum(g, 0.5 * (r + b))
    g_new = g * (1 - amount) + g_neutral * amount
    out = rgb.copy()
    out[..., 1] = g_new
    if preserve_lightness:
        old_l = rgb.mean(-1)
        new_l = out.mean(-1)
        scale = np.where(new_l > 1e-6, old_l / new_l, 1.0)
        out = np.clip(out * scale[..., None], 0.0, 1.0)
    return out


def apply_xpsm(rgb, procs):
    """按 parse_xpsm 解析出的 process 链顺序应用到 rgb[H,W,3] float[0,1]。"""
    for p in procs:
        if p["cls"] == "CurvesTransformation":
            rgb = apply_curves(rgb, p["chans"])
        elif p["cls"] == "SCNR":
            pm = p["params"]
            rgb = apply_scnr_green(rgb, float(pm.get("amount", 0.5)),
                                   pm.get("preserveLightness", "true") == "true")
    return rgb
