"""pi_curves_scnr（Python 复现 PixInsight CurvesTransformation + SCNR）单元测试。

不变量 + 与真 PI 的等价性（后者已离线 eval：24 张银心/散布 tile，逐像素 mean≈3.6/255、
p99≈11，视觉等价；eval 数据不进 git，这里测可合成验证的不变量）。
"""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import pi_curves_scnr as pcs
import pixinsight_batch as pb

XPSM = os.path.join(os.path.dirname(__file__), "..", "skills", "batch_process_frames.xpsm")


def test_identity_curve_is_noop():
    """恒等曲线 [[0,0],[1,1]] 不改值。"""
    x = np.linspace(0, 1, 50)
    assert np.allclose(pcs._apply_curve(x, [[0, 0], [1, 1]]), x)


def test_curve_monotonic_and_bounded():
    """xpsm 里的 K/B/b 曲线应单调不减、输出落 [0,1]（提亮曲线的基本性质）。"""
    procs = pb.parse_xpsm(XPSM)
    x = np.linspace(0, 1, 256)
    for p in procs:
        if p["cls"] != "CurvesTransformation":
            continue
        for cid, pts in p["chans"].items():
            y = pcs._apply_curve(x, pts)
            assert y.min() >= 0.0 and y.max() <= 1.0, f"{cid} 越界"
            assert np.all(np.diff(y) >= -1e-6), f"{cid} 非单调"


def test_curve_endpoints_fixed():
    """曲线端点 (0,0)/(1,1) 保持——黑场和白点不漂。"""
    procs = pb.parse_xpsm(XPSM)
    for p in procs:
        if p["cls"] != "CurvesTransformation":
            continue
        for cid, pts in p["chans"].items():
            y = pcs._apply_curve(np.array([0.0, 1.0]), pts)
            assert abs(y[0] - 0.0) < 1e-6 and abs(y[1] - 1.0) < 1e-6


def test_scnr_removes_green_cast():
    """SCNR AverageNeutral：偏绿像素的 G 被压到不超过 (R+B)/2 方向。"""
    rgb = np.array([[[0.2, 0.9, 0.2]]], float)        # 强绿
    out = pcs.apply_scnr_green(rgb, amount=1.0, preserve_lightness=False)
    assert out[0, 0, 1] <= 0.5 * (rgb[0, 0, 0] + rgb[0, 0, 2]) + 1e-6
    # 非绿像素（G 本就低）不被抬高
    rgb2 = np.array([[[0.8, 0.1, 0.8]]], float)
    out2 = pcs.apply_scnr_green(rgb2, amount=1.0, preserve_lightness=False)
    assert out2[0, 0, 1] <= rgb2[0, 0, 1] + 1e-6


def test_scnr_amount_zero_noop():
    """amount=0 时 SCNR 不改图。"""
    rng = np.random.default_rng(0)
    rgb = rng.random((8, 8, 3))
    out = pcs.apply_scnr_green(rgb, amount=0.0, preserve_lightness=False)
    assert np.allclose(out, rgb)


def test_lab_roundtrip_identity():
    """RGB→Lab→RGB 往返（恒等 b* 曲线）应近似还原——保证 b* 通道不引入色偏。"""
    rng = np.random.default_rng(1)
    rgb = rng.random((16, 16, 3))
    lab = pcs._rgb2lab(rgb)
    back = pcs._lab2rgb(lab)
    assert np.abs(back - rgb).max() < 1e-3


def test_apply_xpsm_shape_and_range():
    """整链 apply_xpsm 输出形状不变、值落 [0,1]、且确实改了图（非 no-op）。"""
    procs = pb.parse_xpsm(XPSM)
    rng = np.random.default_rng(2)
    rgb = rng.random((32, 32, 3)) * 0.5      # 偏暗，调色应提亮
    out = pcs.apply_xpsm(rgb.copy(), procs)
    assert out.shape == rgb.shape
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert not np.allclose(out, rgb)         # 调色生效
    assert out.mean() > rgb.mean()           # 净效果提亮（K 曲线提亮）


def test_apply_xpsm_brightens_blue_most():
    """xpsm 净效果：蓝通道提升幅度 >= 红通道（B 曲线 + b* 都加蓝，复刻真 PI 的提蓝）。"""
    procs = pb.parse_xpsm(XPSM)
    gray = np.full((16, 16, 3), 0.3)          # 中性灰
    out = pcs.apply_xpsm(gray.copy(), procs)
    d_r = out[..., 0].mean() - 0.3
    d_b = out[..., 2].mean() - 0.3
    assert d_b >= d_r - 1e-6, f"蓝提升 {d_b:.3f} 应 >= 红 {d_r:.3f}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
