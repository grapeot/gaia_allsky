"""gaia_allsky 渲染管线物理正确性测试。

覆盖: 星等→亮度、B-V→星色、投影、3D reproject(星座散架+平方反比)、
skyglow 加性梯度、L 轨迹连续性、tonemap 编码、银河涌现密度比。
"""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import render_starmap as rs
import render_3d as r3
import render_horizon as rh
import render_big_dipper_video as bdv
import render_vr_video as rvv
import video_common as vc
import motion

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


# ---------- 基础物理: 星等→亮度, B-V→星色 ----------

def test_mag_to_luminance_5mag_is_100x():
    """星等差 5 等 = 亮度差 100 倍(普森公式)。"""
    L = rs.mag_to_luminance(np.array([0.0, 5.0]))
    assert np.isclose(L[0] / L[1], 100.0)


def test_mag_to_luminance_ref_anchors_unity():
    """m_ref 处亮度归一为 1。"""
    assert np.isclose(rs.mag_to_luminance(np.array([8.0]), m_ref=8.0)[0], 1.0)


def test_bv_to_rgb_blue_vs_red():
    """蓝白星(B-V<0)蓝分量 > 红; 橙红星(B-V>1.4)红 > 蓝。"""
    blue = rs.bv_to_rgb(np.array([-0.3]))[0]
    red = rs.bv_to_rgb(np.array([1.6]))[0]
    assert blue[2] > blue[0]      # 蓝白星: B > R
    assert red[0] > red[2]        # 橙红星: R > B


# ---------- 投影 ----------

def test_mollweide_center_maps_to_center():
    """银心(l=0,b=0)在 Mollweide 落在画面中心附近。"""
    px, py, ins = rs.project_mollweide(np.array([0.0]), np.array([0.0]), 1000, 500)
    assert ins[0]
    assert abs(px[0] - 500) < 5 and abs(py[0] - 250) < 5


def test_mollweide_mask_is_ellipse():
    """Mollweide mask 中心 True, 四角 False(椭圆)。"""
    m = rs.mollweide_mask(200, 100)
    assert m[50, 100]            # 中心
    assert not m[0, 0]           # 左上角


def test_equirect_roundtrip_monotonic():
    """equirectangular: 经度增 → px 增, 纬度增 → py 减(天顶在上)。"""
    px, py, _ = rs.project_equirectangular(np.array([10.0, 200.0]),
                                           np.array([-30.0, 60.0]), 360, 180)
    assert px[1] > px[0]
    assert py[1] < py[0]


# ---------- 3D reproject: 星座散架 + 平方反比 ----------

def test_reproject_inverse_square_brightening():
    """飞近一颗星, 视星等变小(变亮); 飞远变大(变暗)。"""
    xyz = r3._radec_dist_to_xyz(np.array([0.0]), np.array([0.0]), np.array([100.0]))
    g = np.array([5.0])
    # 朝这颗星方向飞 50pc (变近)
    closer = r3.reproject_from(xyz, g, xyz[0] * 0.5)[2][0]
    farther = r3.reproject_from(xyz, g, -xyz[0] * 0.5)[2][0]
    assert closer < 5.0          # 变近 → 更亮(星等更小)
    assert farther > 5.0         # 变远 → 更暗


def test_constellation_breaks_apart():
    """北斗七星沿银道飞 300pc 后, RA 角分布收缩(星座散架)。"""
    # 北斗七颗星 (RA, Dec, dist pc)
    bd = np.array([[165.93, 61.75, 123], [165.46, 56.38, 79], [178.46, 53.69, 84],
                   [183.86, 57.03, 81], [193.51, 55.96, 81], [200.98, 54.93, 83],
                   [206.89, 49.31, 104]])
    xyz = r3._radec_dist_to_xyz(bd[:, 0], bd[:, 1], bd[:, 2])
    g = np.full(7, 2.0)
    obs = r3.flight_direction("galactic_plane") * 300.0
    ra_new = r3.reproject_from(xyz, g, obs)[0]
    spread_before = bd[:, 0].max() - bd[:, 0].min()       # ~41 度
    spread_after = ra_new.max() - ra_new.min()
    assert spread_after < spread_before * 0.5             # 角分布显著收缩


def test_milky_way_unchanged_at_small_flight():
    """飞几百 pc, 远处星(银河主体)方向几乎不变(银河是大尺度结构)。"""
    far = r3._radec_dist_to_xyz(np.array([45.0]), np.array([10.0]), np.array([5000.0]))
    g = np.array([10.0])
    ra0 = r3.reproject_from(far, g, np.zeros(3))[0][0]
    ra1 = r3.reproject_from(far, g, r3.flight_direction("galactic_plane") * 300.0)[0][0]
    assert abs(ra1 - ra0) < 5.0                           # 远星方向变化 < 5 度


# ---------- skyglow 光污染: 加性梯度 ----------

def test_skyglow_monotonic_increasing():
    """Bortle 等级越高(污染越重), 辉光越强。"""
    vals = [rh.skyglow_level(b) for b in range(1, 10)]
    assert all(vals[i] < vals[i + 1] for i in range(8))


def test_skyglow_additive_floods_milky_way():
    """高 Bortle 辉光基底 > 银河带典型亮度(淹没银河)。"""
    assert rh.skyglow_level(9) > rh.skyglow_level(1) * 10   # 市中心 >> 荒漠


# ---------- 地平坐标变换 ----------

def test_galactic_center_culmination_altitude():
    """北京(39.9°N)银心上中天高度 = 90 − lat − |dec| ≈ 21°(几何)。"""
    # 银心 l=0,b=0; LST=银心RA 时上中天
    from astropy.coordinates import SkyCoord, Galactic
    import astropy.units as u
    gc = SkyCoord(l=0 * u.deg, b=0 * u.deg, frame=Galactic).icrs
    az, alt = rh.gal_to_altaz(np.array([0.0]), np.array([0.0]), 39.9, gc.ra.hour)
    assert abs(alt[0] - (90 - 39.9 - abs(gc.dec.deg))) < 1.0
    assert abs(az[0] - 180.0) < 2.0                        # 上中天在正南


# ---------- L 轨迹 ----------

def test_l_trajectory_continuous():
    """L 轨迹首尾停顿, 中段连续(相邻帧位移有界, 无跳变)。"""
    traj = r3.l_trajectory(100, leg1_pc=400, leg2_pc=2000)
    assert len(traj) == 100
    steps = np.linalg.norm(np.diff(traj, axis=0), axis=1)
    assert steps.max() < 100.0                            # 单帧位移有界(平滑)
    assert np.isclose(np.linalg.norm(traj[0]), 0.0)       # 起点在原点


def test_l_trajectory_two_legs_orthogonal():
    """L 两段方向正交(沿银道 ⊥ 垂直银道)。"""
    d1 = r3.flight_direction("galactic_plane")
    d2 = r3.flight_direction("galactic_pole")
    assert abs(np.dot(d1, d2)) < 0.05                     # 近正交


def test_big_dipper_direction_is_unit_vector():
    """北斗默认视线方向是归一化 3D 向量。"""
    d = vc.big_dipper_direction()
    assert d.shape == (3,)
    assert np.isclose(np.linalg.norm(d), 1.0)


def test_parse_triplet_normalizes_direction():
    """CLI 方向覆盖值解析后归一，便于直接作为 look/flight dir。"""
    d = vc.parse_triplet("2,0,0")
    assert np.allclose(d, np.array([1.0, 0.0, 0.0]))


def test_duration_overrides_frames():
    """CLI 支持按 duration*fps 自动计算帧数。"""
    assert vc.resolve_frame_count(10, 60, 2.5) == 150
    assert vc.resolve_frame_count(10, 60, None) == 10


def test_vr_cli_config_uses_equirect_dimensions():
    """VR CLI config 保留 2:1 equirectangular 分辨率参数。"""
    args = rvv.build_parser().parse_args(["--width", "640", "--height", "320", "--frames", "7"])
    cfg = rvv.config_from_args(args)
    assert cfg["width"] == 640
    assert cfg["height"] == 320
    assert cfg["frames"] == 7


def test_vr_cli_duration_sets_frame_count():
    """VR CLI 可用 duration 表达时间分辨率。"""
    args = rvv.build_parser().parse_args(["--fps", "60", "--duration", "10"])
    cfg = rvv.config_from_args(args)
    assert cfg["frames"] == 600
    assert cfg["positions"].shape == (600, 3)


def test_big_dipper_cli_default_camera_starts_at_dipper_then_gc():
    """前向版本默认先看北斗并朝北斗走，第二段看银心。"""
    args = bdv.build_parser().parse_args(["--frames", "7"])
    cfg = bdv.config_from_args(args)
    assert cfg["positions"].shape == (7, 3)
    assert cfg["look_dirs"].shape == (7, 3)
    assert np.isclose(np.linalg.norm(cfg["look_dirs"][0]), 1.0)
    assert np.isclose(np.linalg.norm(cfg["look_dirs"][-1]), 1.0)
    assert np.dot(cfg["look_dirs"][0], vc.big_dipper_direction()) > 0.99
    assert np.dot(cfg["look_dirs"][-1], r3.flight_direction("galactic_plane")) > 0.99
    first_leg_dir = cfg["positions"][3] / np.linalg.norm(cfg["positions"][3])
    assert np.dot(first_leg_dir, vc.big_dipper_direction()) > 0.99


def test_shared_l_motion_has_two_legs():
    """共享 L motion 第一段沿银道，第二段沿银北极。"""
    positions, phase = motion.l_motion(11, leg1_pc=400, leg2_pc=1000, split=0.5)
    d1 = r3.flight_direction("galactic_plane")
    d2 = r3.flight_direction("galactic_pole")
    assert np.dot(positions[5] / np.linalg.norm(positions[5]), d1) > 0.99
    delta2 = positions[-1] - positions[5]
    assert np.dot(delta2 / np.linalg.norm(delta2), d2) > 0.99
    assert phase[0] == 0
    assert phase[-1] == 1


def test_perspective_render_fills_rectangular_frame():
    """Perspective 前向相机返回满画幅矩形，不是鱼眼圆盘。"""
    xyz = r3._radec_dist_to_xyz(np.array([0.0]), np.array([0.0]), np.array([100.0]))
    g = np.array([2.0])
    bv = np.array([0.7])
    frame = r3.render_perspective_lookdir(xyz, g, bv, np.zeros(3), np.array([1.0, 0.0, 0.0]), 80, 40)
    assert frame.shape == (40, 80, 3)
    assert frame.sum() > 0


# ---------- tonemap 编码 ----------

def test_normalize_curves_expand_shadows():
    """log 编码暗部抬升 > gamma > linear(信息密度: log 最适合 16bit 底片)。"""
    cv = np.zeros((10, 10, 3), np.float32)
    cv[0, 0] = 100.0      # 一个亮点
    cv[5, 5] = 1.0        # 一个暗点
    lin = rs.normalize_brightness(cv, 99.0, "linear")[5, 5, 0]
    gam = rs.normalize_brightness(cv, 99.0, "gamma")[5, 5, 0]
    log = rs.normalize_brightness(cv, 99.0, "log")[5, 5, 0]
    assert log > gam > lin                                # 暗点被抬升程度


def test_tonemap_hdr16_dtype_range():
    """HDR tonemap 输出 uint16, 在 [0, 65535]。"""
    cv = np.random.RandomState(0).rand(20, 20, 3).astype(np.float32)
    out = rs.tonemap_hdr16(cv, curve="log")
    assert out.dtype == np.uint16
    assert out.max() <= 65535 and out.min() >= 0


def test_tonemap_handles_all_black():
    """全黑画布不崩溃(percentile fallback)。"""
    cv = np.zeros((10, 10, 3), np.float32)
    out = rs.tonemap_sdr(cv)
    assert out.max() == 0


# ---------- 银河涌现(集成测试, 需数据) ----------

@pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "gaia_g8.npz")),
                    reason="需要 gaia_g8.npz 数据")
def test_milky_way_emerges_density():
    """银道面(|b|<10)恒星密度 > 高银纬(|b|>70)——银河从真实密度涌现。"""
    d = np.load(os.path.join(DATA, "gaia_g8.npz"))
    b = d["b"]
    import math
    # 按立体角归一(|b|<10 带占 sin10°·2 球面; |b|>70 冠占 1−sin70°)
    dens_plane = (np.abs(b) < 10).sum() / (math.sin(math.radians(10)) * 2)
    dens_high = (np.abs(b) > 70).sum() / (1 - math.sin(math.radians(70)))
    assert dens_plane > dens_high * 1.3                   # 银道面显著更密(银河涌现)
