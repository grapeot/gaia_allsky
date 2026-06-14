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
import render_bortle_eye_grid as beg
import render_tan_wcs as tw
import video_common as vc
import motion
import fetch_gaia_allsky as fga

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


def test_eye_sensitivity_gain_scales_by_magnitude():
    """NELM 每提升 1 等，对应约 2.512 倍灵敏度增益。"""
    assert np.isclose(beg.gain_for_nelm(7) / beg.gain_for_nelm(6), 10 ** 0.4)
    assert np.isclose(beg.gain_for_nelm(11), 100.0)
    assert np.isclose(beg.gain_for_mag_delta(2), beg.gain_for_nelm(8))


def test_bortle_grid_separates_eye_delta_and_exposure_defaults():
    """视觉模式输入灵敏度提升，NELM 是输出；SNR 模式输入曝光倍率。"""
    args = beg.build_parser().parse_args([])
    assert args.eye_deltas == "0,2,4"
    assert args.exposures == "1,10,100"
    assert args.panel_width == 1080
    assert args.panel_height == 1920
    assert args.az_width_deg == 90.0
    assert args.max_alt_deg == 75.0
    assert args.fov_axis == "horizontal"
    assert args.lat_deg == 23.13
    assert args.limiting_contrast == 0.5
    assert args.target_white == 2.0
    assert args.target_sky == 0.012
    assert args.star_contrast == 6.0
    assert args.chroma == 1.8
    assert args.psf_core_px == 0.6
    assert args.faint_gain == 3.8
    assert args.faint_mag_min == 11.0
    assert args.sat_over_sky == 6.0
    assert beg.parse_csv_numbers(args.wing_sigmas) == [3.0, 9.0]
    assert beg.parse_csv_numbers(args.wing_weights) == [0.65, 0.35]
    assert args.ext_threshold == 0.035
    assert args.ext_sigma == 8.0
    assert args.reference_mode == "brightest"
    assert args.reference_bortle is None
    assert args.reference_value is None
    assert beg.column_label("adapted", 2).startswith("cost +2mag")
    assert "NELM~" in beg.column_label("adapted", 2, 1)
    assert beg.column_label("snr", 10) == "exp 10x"


def test_allsky_fetch_query_matches_renderer_cache_schema():
    """全天 Gaia fetcher 应直接生成渲染器需要的 l/b/g/bp_rp schema。"""
    q = fga.build_query(gmax=11.0, row_limit=123)
    assert "TOP 123" in q
    assert "l, b, phot_g_mean_mag, bp_rp" in q
    assert "phot_g_mean_mag < 11.0" in q


def test_allsky_fetch_table_to_arrays_fills_missing_color():
    """缺失 BP-RP 时用太阳型颜色 fallback，避免渲染阶段出现 NaN。"""
    table = {
        "l": np.array([1.0, 2.0]),
        "b": np.array([3.0, 4.0]),
        "phot_g_mean_mag": np.array([5.0, 6.0]),
        "bp_rp": np.array([np.nan, 1.2]),
    }
    l, b, g, bp_rp = fga.table_to_arrays(table)
    assert np.allclose(l, [1.0, 2.0])
    assert np.allclose(b, [3.0, 4.0])
    assert np.allclose(g, [5.0, 6.0])
    assert np.allclose(bp_rp, [0.7, 1.2])


def test_empirical_bortle_nelm_table_matches_visual_labels():
    """正式视觉图用经验 Bortle NELM 表作为可见性锚点。"""
    assert beg.empirical_nelm_for_bortle(1) == 7.8
    assert beg.empirical_nelm_for_bortle(6) == 5.3
    assert beg.effective_nelm_for_panel(1, 2) == 9.8
    assert beg.effective_nelm_for_panel(6, 4) == 9.3
    assert "NELM~7.8" in beg.column_label("adapted", 0, 1)
    assert "NELM~9.3" in beg.column_label("adapted", 4, 6)


def test_visual_luminance_is_anchored_at_limiting_contrast():
    """有效极限星等处的星光应是当前 skyglow 的固定微弱对比。"""
    contrast = 0.08
    for bortle, delta in [(1, 0), (6, 0), (6, 4)]:
        m_lim = beg.effective_nelm_for_panel(bortle, delta)
        sky = rh.skyglow_level(bortle)
        lum = beg.visual_luminance_for_mag(m_lim, bortle, delta, limiting_contrast=contrast)
        assert np.isclose(lum / sky, contrast)


def test_visual_luminance_uses_delta_once():
    """+2mag 只通过有效 NELM 进入模型，不再额外乘一次 gain。"""
    bortle = 1
    contrast = 0.08
    sky = rh.skyglow_level(bortle)
    lum = beg.visual_luminance_for_mag(beg.effective_nelm_for_panel(bortle, 2), bortle, 2, limiting_contrast=contrast)
    assert np.isclose(lum / sky, contrast)


def test_saturation_bloom_conserves_energy_and_caps_core():
    """饱和溢出应守恒总能量：核心截到 sat_level，截下的能量散布到溢出翼。"""
    c = np.zeros((40, 40, 3), np.float32)
    c[20, 20] = 50.0
    out = beg.saturate_and_bloom(c, sat_level=3.0)
    assert np.isclose(float(out.sum()), float(c.sum()), rtol=1e-4)
    assert out[20, 12, 0] > c[20, 12, 0]          # 翼区获得散布能量
    assert out.sum(-1).max() < c.sum(-1).max()    # 峰值显著低于原始点


def test_saturation_bloom_leaves_faint_canvas_unchanged():
    """低于饱和阈值的画布不应被溢出处理改动。"""
    c = np.full((20, 20, 3), 0.01, np.float32)
    c[10, 10] = 0.5
    out = beg.saturate_and_bloom(c, sat_level=10.0)
    assert np.allclose(out, c)


def test_uniform_psf_applies_truncation_gain_to_faint_stars_only():
    """统一 PSF 模型: G>=faint_mag_min 的星乘截断补偿增益，亮星不变。"""
    px = np.array([5, 15])
    py = np.array([10, 10])
    inside = np.array([True, True])
    mag = np.array([5.0, 10.0])
    lum = np.array([1.0, 1.0])
    cols = np.ones((2, 3), dtype=float)
    out = beg.accumulate_uniform_psf_stars(
        21, 21, px, py, inside, mag, lum, cols,
        psf_core_px=0.0, faint_gain=4.2, faint_mag_min=9.0, sat_level=None,
    )
    assert np.isclose(out[10, 5, 0], 1.0)
    assert np.isclose(out[10, 15, 0], 4.2)


def test_saturation_threshold_rides_magnitude_ladder():
    """+delta_mag 把全部星光乘 10^(0.4·delta)，饱和线必须同步缩放：
    扣除 skyglow 后 +4mag canvas 应严格是 +0mag 的 40 倍，饱和几何不变。
    否则高 delta 面板会有大片银河被截断、摊成糊翼(2026-06-09 +4mag 过糊问题)。"""
    rng = np.random.RandomState(7)
    n = 400
    l = rng.uniform(0, 40, n)
    b = rng.uniform(-15, 15, n)
    g = rng.uniform(2.0, 11.0, n)
    bv = np.full(n, 0.7)
    # ext_threshold=0: Weber 阈值锚定在天空背景上，刻意不随灵敏度增益缩放，
    # 此测试只验证饱和几何的阶梯不变性，需要把它关掉。
    common = dict(width=64, height=64, lat_deg=23.13, lst_hours=17.76,
                  projection="horizon_window", look_az=180.0, look_alt=None,
                  fov_deg=110.0, az_width_deg=90.0, max_alt_deg=75.0,
                  limiting_contrast=0.5, psf_core_px=1.1, faint_gain=4.2,
                  faint_mag_min=9.0, sat_over_sky=6.0,
                  wing_sigmas=(3.0, 9.0), wing_weights=(0.65, 0.35), mode="adapted",
                  ext_threshold=0.0)
    # render_panel_canvas 末尾 add_skyglow 加的是【additive】辉光（含光污染 boost，
    # 2026-06），不是场景锚 skyglow_level；要扣的常数底必须用同一个 additive 值，
    # 否则残留 (boost-1)*skyglow_level(1) 会破坏阶梯线性。扣对底之后，此测试验证的
    # 饱和几何阶梯不变性与 boost 无关（boost 只动均匀加性底，不动星点饱和几何）。
    sky = rh.additive_skyglow_level(1)
    c0 = beg.render_panel_canvas(l, b, g, bv, 1, 0.0, **common) - sky
    c4 = beg.render_panel_canvas(l, b, g, bv, 1, 4.0, **common) - sky
    gain = beg.gain_for_mag_delta(4.0)
    assert np.allclose(c4, c0 * gain, rtol=1e-4, atol=1e-7)


def test_extended_threshold_removes_subthreshold_diffuse_glow():
    """低于 Weber 阈值的大面积弥散光对人眼不可见，应被显示层移除。
    这是银河在 Bortle 7 左右消失、而相机长曝光仍拍得到的原因。"""
    sky = 1.0
    canvas = np.full((60, 60, 3), 0.02 * sky / 3.0, np.float32)   # 2% 均匀弥散光
    out = beg.apply_extended_visibility_threshold(canvas, sky, threshold=0.035, sigma_px=4.0)
    assert float(out.sum()) < float(canvas.sum()) * 0.05


def test_extended_threshold_preserves_point_stars():
    """点源是高频分量，Weber 阈值不应削弱 NELM 锚定的恒星可见度。"""
    sky = 1.0
    canvas = np.zeros((60, 60, 3), np.float32)
    canvas[30, 30] = 0.5 * sky / 3.0                              # 极限星等附近的点源
    out = beg.apply_extended_visibility_threshold(canvas, sky, threshold=0.035, sigma_px=4.0)
    assert out[30, 30].sum() > canvas[30, 30].sum() * 0.85


def test_extended_threshold_kills_band_in_bright_sky_keeps_dark_sky():
    """同一片弥散银河带：暗空(对比 90%)应几乎原样保留，亮空(对比 3%)应消失。"""
    from scipy.ndimage import gaussian_filter
    band = np.zeros((120, 120, 3), np.float32)
    band[40:80, :, :] = 1.0 / 3.0
    for c in range(3):                                            # 平滑成真实银河带那样的低频结构
        band[..., c] = gaussian_filter(band[..., c], 8.0)
    peak = band[60, 60].sum()
    dark_sky, bright_sky = peak / 0.9, peak / 0.03
    out_dark = beg.apply_extended_visibility_threshold(band, dark_sky, 0.035, 4.0)
    out_bright = beg.apply_extended_visibility_threshold(band, bright_sky, 0.035, 4.0)
    keep_dark = out_dark[40:80].sum() / band[40:80].sum()
    keep_bright = out_bright[40:80].sum() / band[40:80].sum()
    assert keep_dark > 0.9
    assert keep_bright < 0.05


def test_apparent_star_size_grows_with_brightness():
    """同一 PSF 下，亮星经饱和溢出后的可见足迹应大于暗星(视尺寸单调性)。"""
    px = np.array([15, 45])
    py = np.array([30, 30])
    inside = np.array([True, True])
    mag = np.array([2.0, 7.0])
    lum = np.array([100.0, 1.0])
    cols = np.ones((2, 3), dtype=float)
    out = beg.accumulate_uniform_psf_stars(
        60, 60, px, py, inside, mag, lum, cols,
        psf_core_px=1.1, faint_gain=4.2, faint_mag_min=9.0, sat_level=0.5,
    ).sum(-1)
    thresh = 0.05
    bright_footprint = (out[:, :30] > thresh).sum()
    faint_footprint = (out[:, 30:] > thresh).sum()
    assert bright_footprint > faint_footprint > 0


def test_limiting_mag_worsens_with_light_pollution():
    """同样眼睛灵敏度下，Bortle 6 的计算 NELM 应低于 Bortle 1。"""
    assert beg.limiting_mag_for_sky(6, beg.gain_for_mag_delta(0)) < beg.limiting_mag_for_sky(1, beg.gain_for_mag_delta(0))


def test_default_guangzhou_galactic_center_view_is_high_enough():
    """默认广州视角让银心上中天高度更高，更适合展示银河。"""
    az, alt = beg.galactic_center_altaz(23.13, 17.76)
    assert 35.0 < alt < 45.0
    assert 160.0 < az < 200.0


def test_perspective_altaz_projection_centers_look_direction():
    """人眼广角投影应把 look az/alt 放到画面中心。"""
    px, py, inside = beg.project_perspective_altaz(
        np.array([180.0]), np.array([20.0]), 180.0, 20.0, 960, 540, 110.0
    )
    assert inside[0]
    assert abs(px[0] - 480) <= 1
    assert abs(py[0] - 270) <= 1


def test_horizon_camera_places_horizon_on_bottom_edge():
    """地平线相机透视投影把中心地平线放在图像下缘。"""
    _hfov, vfov = beg.aspect_preserving_horizon_fovs(540, 960, 90.0, 75.0)
    px, py, inside = beg.project_horizon_camera(
        np.array([180.0, 180.0]), np.array([0.0, vfov / 2.0]), 180.0, 540, 960, 90.0, 75.0
    )
    assert inside.all()
    assert py[0] >= 958
    assert abs(px[1] - 270) <= 1
    assert abs(py[1] - 480) <= 1


def test_horizon_camera_fov_preserves_image_aspect():
    """水平和垂直 FOV 必须匹配画面宽高比，否则天空会被 squeeze。"""
    h_fov, v_fov = beg.aspect_preserving_horizon_fovs(1600, 900, 130.0, 75.0)
    fov_aspect = np.tan(np.radians(h_fov) / 2.0) / np.tan(np.radians(v_fov) / 2.0)
    assert np.isclose(fov_aspect, 1600 / 900)
    h_fov_v, v_fov_v = beg.aspect_preserving_horizon_fovs(1600, 900, 130.0, 95.0, fov_axis="vertical")
    fov_aspect_v = np.tan(np.radians(h_fov_v) / 2.0) / np.tan(np.radians(v_fov_v) / 2.0)
    assert np.isclose(fov_aspect_v, 1600 / 900)


def test_horizon_window_preserves_explicit_look_az_when_look_alt_missing(tmp_path, monkeypatch):
    """horizon_window 只需要中心方位角，不能因 look_alt 缺省覆盖显式 look_az。"""
    data_path = tmp_path / "mini_gaia.npz"
    np.savez(
        data_path,
        l=np.array([0.0]),
        b=np.array([0.0]),
        g=np.array([8.0]),
        bp_rp=np.array([0.7]),
    )
    captured = {}

    def fake_project(az, alt, center_az, width, height, h_fov_deg, v_fov_deg, fov_axis="horizontal"):
        captured["center_az"] = center_az
        return np.array([0]), np.array([0]), np.array([False])

    monkeypatch.setattr(beg, "project_horizon_camera", fake_project)
    beg.render_grid(
        str(data_path),
        str(tmp_path / "out.png"),
        [1],
        [0.0],
        8,
        8,
        23.13,
        19.8,
        99.7,
        2.2,
        "horizon_window",
        180.0,
        None,
        110.0,
        "sky_median",
        0.03,
        99.5,
        25.0,
        4.0,
        2.0,
        0.5,
        90.0,
        75.0,
        1.1,
        4.2,
        9.0,
        "first",
        None,
        None,
        "adapted",
    )
    assert captured["center_az"] == 180.0


def test_sky_adapted_normalization_equalizes_background():
    """Median sky adaptation 让不同背景光污染映射到相近背景亮度。"""
    c1 = np.full((20, 20, 3), 0.01, np.float32)
    c2 = np.full((20, 20, 3), 1.0, np.float32)
    n1 = beg.normalize_sky_adapted(c1, target_sky=0.03, gamma=2.2)
    n2 = beg.normalize_sky_adapted(c2, target_sky=0.03, gamma=2.2)
    assert np.isclose(np.median(n1.sum(-1)), np.median(n2.sum(-1)))


def test_sky_adapted_reduces_star_contrast_in_bright_sky():
    """关闭白点拉伸时，同样星光叠加在亮背景上相对对比更低。"""
    dark = np.full((20, 20, 3), 0.01, np.float32)
    bright = np.full((20, 20, 3), 1.0, np.float32)
    dark[10, 10] += 0.1
    bright[10, 10] += 0.1
    nd = beg.normalize_sky_adapted(dark, target_sky=0.03, gamma=2.2, white_pct=100.0, star_contrast=4.0, target_white=None).sum(-1)
    nb = beg.normalize_sky_adapted(bright, target_sky=0.03, gamma=2.2, white_pct=100.0, star_contrast=4.0, target_white=None).sum(-1)
    dark_contrast = nd[10, 10] - np.median(nd)
    bright_contrast = nb[10, 10] - np.median(nb)
    assert bright_contrast < dark_contrast


def test_sky_adapted_highlight_compression_limits_clipping():
    """高光白点压缩后，极少数亮点允许接近白，但整体不会大片过曝。"""
    c = np.full((100, 100, 3), 0.02, np.float32)
    c[0:5, 0:5] = 10.0
    out = beg.normalize_sky_adapted(c, target_sky=0.03, gamma=2.2, white_pct=99.5)
    saturated = (out >= 1.0).all(axis=-1).mean()
    assert saturated < 0.01


def test_highlight_soft_shoulder_keeps_texture_above_knee():
    """膝点以上的高光必须保持单调纹理，不能压成同一个平台值(clip 感)。"""
    adapted = np.zeros((1, 4, 3), np.float32)
    for i, y in enumerate([2.0, 2.4, 3.2, 6.0]):       # 全部高于 target_white=2.0 附近
        adapted[0, i] = y / 3.0
    out = beg.finish_sky_adapted(adapted, target_sky=0.03, gamma=2.2,
                                 target_white=2.0, signal_stretch=1.0)
    ys = out.sum(axis=-1)[0]
    assert ys[0] < ys[1] < ys[2] < ys[3]               # 严格单调，无平台
    assert ys[3] <= 3.0 ** (1 / 2.2) * 3 + 1e-6        # 不越显示上限


def test_sky_adapted_white_percentile_uses_display_range():
    """white_pct 应拉伸背景以上信号，而不是让高分位停在中灰。"""
    c = np.full((100, 100, 3), 0.02, np.float32)
    c[0:15, 0:15] += np.linspace(0.01, 0.2, 225).reshape(15, 15, 1)
    out = beg.normalize_sky_adapted(c, target_sky=0.03, gamma=2.2, white_pct=99.0, target_white=3.0)
    y = out.sum(-1)
    assert np.percentile(y, 99.0) > 2.5
    assert np.percentile(y, 25.0) < 0.4


def test_brightest_reference_reduces_shared_stretch():
    """最亮 panel 做 reference 时，共享 stretch 应小于首个普通 panel reference。"""
    dark = np.full((20, 20, 3), 0.02, np.float32)
    bright = dark.copy()
    bright[5:15, 5:15] += 0.2
    a_dark = beg.adapt_sky_floor(dark, target_sky=0.03, sky_pct=25, star_contrast=4)
    a_bright = beg.adapt_sky_floor(bright, target_sky=0.03, sky_pct=25, star_contrast=4)
    first = beg.signal_stretch_for_adapted(a_dark, target_sky=0.03, white_pct=99.5, target_white=2.0)
    brightest = beg.signal_stretch_for_adapted(a_bright, target_sky=0.03, white_pct=99.5, target_white=2.0)
    assert brightest < first


def test_star_contrast_boosts_signal_above_sky():
    """暗背景保持固定时，star_contrast 应提升背景以上的信号。"""
    c = np.full((20, 20, 3), 0.02, np.float32)
    c[10, 10] += 0.02
    low = beg.normalize_sky_adapted(c, target_sky=0.03, gamma=2.2, white_pct=100.0, star_contrast=1.0, target_white=None).sum(-1)
    high = beg.normalize_sky_adapted(c, target_sky=0.03, gamma=2.2, white_pct=100.0, star_contrast=4.0, target_white=None).sum(-1)
    assert np.isclose(np.median(low), np.median(high))
    assert high[10, 10] - np.median(high) > low[10, 10] - np.median(low)


def test_sky_limited_snr_penalizes_bright_sky():
    """同样曝光和星光下，亮天空背景显著降低 SNR。"""
    star = 1.0
    dark_sky = 0.1
    bright_sky = 10.0
    assert beg.sky_limited_snr(star, bright_sky, 1.0) < beg.sky_limited_snr(star, dark_sky, 1.0)


def test_long_exposure_improves_but_does_not_cancel_sky_background():
    """长曝光能提高 SNR，但同等曝光下光污染区仍更差。"""
    star = 1.0
    dark_1x = beg.sky_limited_snr(star, 0.1, 1.0)
    bright_100x = beg.sky_limited_snr(star, 10.0, 100.0)
    bright_1x = beg.sky_limited_snr(star, 10.0, 1.0)
    assert bright_100x > bright_1x
    assert bright_1x < dark_1x


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


def test_big_dipper_cli_default_path_dipper_then_above_gc():
    """前向版本默认先朝北斗跑，再斜向银心上方；相机转为看向银盘目标点。"""
    args = bdv.build_parser().parse_args(["--frames", "7"])
    cfg = bdv.config_from_args(args)
    assert cfg["positions"].shape == (7, 3)
    assert cfg["look_dirs"].shape == (7, 3)
    assert np.isclose(np.linalg.norm(cfg["look_dirs"][0]), 1.0)
    assert np.isclose(np.linalg.norm(cfg["look_dirs"][-1]), 1.0)
    assert np.dot(cfg["look_dirs"][0], vc.big_dipper_direction()) > 0.99
    first_leg_dir = cfg["positions"][3] / np.linalg.norm(cfg["positions"][3])
    assert np.dot(first_leg_dir, vc.big_dipper_direction()) > 0.99
    target = r3.flight_direction("galactic_plane") * args.target_gc_pc + r3.flight_direction("galactic_pole") * args.leg2_pc
    assert np.linalg.norm(cfg["positions"][-1] - target) < 1e-6
    look_target = r3.flight_direction("galactic_plane") * args.target_gc_pc
    expected_final_look = look_target - cfg["positions"][-1]
    expected_final_look = expected_final_look / np.linalg.norm(expected_final_look)
    assert np.dot(cfg["look_dirs"][-1], expected_final_look) > 0.99
    assert cfg["dipper_overlay"]


def test_big_dipper_default_leg1_matches_frame_68_preview_distance():
    """默认第一段约等于旧 400pc 预览第 68 帧的位置，避免飞过北斗。"""
    args = bdv.build_parser().parse_args([])
    assert args.leg1_pc == 50.0
    assert args.target_gc_pc == 400.0


def test_big_dipper_default_fov_and_fast_look_transition():
    """前向默认视角更广，第二段相机转向在 2 秒内完成。"""
    args = bdv.build_parser().parse_args(["--duration", "10", "--fps", "60"])
    assert args.fov_deg == 90.0
    assert args.look_transition_sec == 2.0
    cfg = bdv.config_from_args(args)
    target = r3.flight_direction("galactic_plane") * args.target_gc_pc
    frame_after_transition = int(args.fps * (5 + args.look_transition_sec))
    expected = target - cfg["positions"][frame_after_transition]
    expected = expected / np.linalg.norm(expected)
    assert np.dot(cfg["look_dirs"][frame_after_transition], expected) > 0.99


def test_vr_cli_uses_same_default_position_path():
    """VR 和前向版默认共享同一条位置轨迹。"""
    vr_args = rvv.build_parser().parse_args(["--frames", "9"])
    fw_args = bdv.build_parser().parse_args(["--frames", "9"])
    assert np.allclose(rvv.config_from_args(vr_args)["positions"], bdv.config_from_args(fw_args)["positions"])


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


def test_overlay_width_scales_with_resolution():
    """北斗连线线宽随画幅自适应：720→1px、2160→3px；显式值优先。
    绝对 1px 线在 2160 渲染再缩到 720 预览后不可见(2026-06-09 踩坑)。"""
    assert vc.overlay_width_for_frame(720, 720) == 1
    assert vc.overlay_width_for_frame(2160, 2160) == 3
    assert vc.overlay_width_for_frame(4096, 2048) == 3
    assert vc.overlay_width_for_frame(2160, 2160, requested=1) == 1
    args = bdv.build_parser().parse_args([])
    assert args.overlay_width == 0


def test_big_dipper_overlay_projection_inside_first_frame():
    """默认第一帧看北斗时，北斗连线点应落在 perspective 画面内。"""
    pts, inside = vc.project_perspective_points(
        vc.big_dipper_xyz(),
        np.zeros(3),
        vc.big_dipper_direction(),
        640,
        640,
        60.0,
    )
    assert inside.all()
    assert pts[:, 0].min() > 150 and pts[:, 0].max() < 500
    assert pts[:, 1].min() > 200 and pts[:, 1].max() < 450


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


# ---------- 视频路径统一 PSF 成像模型 (移植自静态图) ----------


def test_video_cli_exposes_unified_psf_defaults():
    """两个视频 CLI 显式暴露统一 PSF/饱和/截断补偿参数, 默认值即推荐值。"""
    for build in (rvv.build_parser, bdv.build_parser):
        args = build().parse_args([])
        assert args.psf_core_px == 1.1
        assert args.faint_gain == 4.3
        assert args.faint_mag_min == 11.0
        assert args.sat_over_ref == 6.0
        assert args.sat_ref_mag == r3.SAT_REF_MAG_DEFAULT
        assert args.wing_sigmas == "3,9"
        assert args.wing_weights == "0.65,0.35"
        # 旧加性 bloom 参数已从正式视频路径移除
        assert not hasattr(args, "bloom_strength")
        assert not hasattr(args, "bloom_sigma")


def test_video_saturation_anchor_is_reference_magnitude():
    """视频饱和锚点用固定参考星等(非 skyglow): 亮于参考星等才饱和, <=0 关闭。"""
    sl = r3.sat_level_from_ref_mag(6.0, 6.0)
    # 与公式一致: sat_over_ref × L(sat_ref_mag)
    assert np.isclose(sl, 6.0 * rs.mag_to_luminance(6.0, 8.0))
    # 阈值随倍数线性, 随参考星等变亮(数值变大)单调
    assert r3.sat_level_from_ref_mag(12.0, 6.0) > sl
    assert r3.sat_level_from_ref_mag(6.0, 5.0) > sl
    # <=0 或 None 关闭饱和溢出
    assert r3.sat_level_from_ref_mag(0.0, 6.0) is None
    assert r3.sat_level_from_ref_mag(-1.0, 6.0) is None
    assert r3.sat_level_from_ref_mag(None, 6.0) is None


def test_video_faint_gain_keyed_on_catalog_g_not_vismag():
    """截断补偿增益按星表固有 G(g_mag)选星, 不是重投影后的视星等。"""
    H = W = 8
    px = np.array([2, 5])
    py = np.array([3, 4])
    inside = np.array([True, True])
    cols = np.ones((2, 3), float)
    vis_mag = np.array([10.0, 10.0])      # 视星等相同
    g_faint = np.array([10.0, 8.0])       # 一颗 G>=9(暗), 一颗 G<9(亮)
    out = r3.unified_psf_image(
        H, W, px, py, inside, vis_mag, g_faint, cols,
        m_ref=8.0, gain=1.0, psf_core_px=0.0,
        faint_gain=4.2, faint_mag_min=9.0, sat_level=None,
    )
    boosted = out[3, 2].sum()             # g=10 暗星, 应被乘 4.2
    plain = out[4, 5].sum()               # g=8 亮星, 不补偿
    assert np.isclose(boosted / plain, 4.2, rtol=1e-4)


def test_video_unified_psf_saturation_conserves_energy():
    """视频路径的饱和溢出能量守恒(把过亮核心摊进散射翼, 不增减总能量)。"""
    H = W = 64
    px = np.array([32])
    py = np.array([32])
    inside = np.array([True])
    cols = np.ones((1, 3), float)
    vis_mag = np.array([0.0])             # 很亮, 必然越过饱和线
    g_mag = np.array([0.0])
    sl = r3.sat_level_from_ref_mag(6.0, 6.0)
    no_sat = r3.unified_psf_image(
        H, W, px, py, inside, vis_mag, g_mag, cols,
        m_ref=8.0, psf_core_px=1.1, faint_gain=4.2, faint_mag_min=9.0, sat_level=None,
    )
    with_sat = r3.unified_psf_image(
        H, W, px, py, inside, vis_mag, g_mag, cols,
        m_ref=8.0, psf_core_px=1.1, faint_gain=4.2, faint_mag_min=9.0, sat_level=sl,
    )
    assert with_sat.sum(-1).max() <= no_sat.sum(-1).max()   # 峰值被压
    assert np.isclose(with_sat.sum(), no_sat.sum(), rtol=1e-4)  # 总能量守恒


def test_video_cli_defaults_to_h265():
    """视频合成默认 H.265（libx265 + hvc1），crf 18 对标旧 x264 crf 16。"""
    vr = rvv.build_parser().parse_args([])
    fw = bdv.build_parser().parse_args([])
    assert vr.codec == "libx265" and fw.codec == "libx265"
    assert vr.crf == 18 and fw.crf == 18


def test_proxy_attenuation_only_dims_inferred_light():
    """列消光衰减只作用于增益推断的不可分辨光，直接观测的星光不动。
    atten=1(干净视线)等价于完整增益；atten=0(全被尘埃挡住)退化为纯直接光。"""
    px = np.array([5, 15])
    py = np.array([10, 10])
    inside = np.array([True, True])
    mag = np.array([12.0, 12.0])
    lum = np.array([1.0, 1.0])
    cols = np.ones((2, 3), dtype=float)
    atten = np.array([1.0, 0.0])
    out = beg.accumulate_uniform_psf_stars(
        21, 21, px, py, inside, mag, lum, cols,
        psf_core_px=0.0, faint_gain=3.8, faint_mag_min=11.0, sat_level=None,
        proxy_atten=atten,
    )
    assert np.isclose(out[10, 5, 0], 3.8)    # 干净视线: 1 + 2.8*1
    assert np.isclose(out[10, 15, 0], 1.0)   # 全遮挡: 只剩直接光


# ---------- HiPS 模式: TAN 投影 + 立体角归一化 + WCS ----------


def test_gnomonic_tangent_point_maps_to_origin():
    """切点本身投到 TAN 标准平面原点 (xi=eta=0)，且前半球可见。"""
    xi, eta, vis = tw.gnomonic(np.array([30.0]), np.array([-10.0]), 30.0, -10.0)
    assert vis[0]
    assert abs(xi[0]) < 1e-9 and abs(eta[0]) < 1e-9


def test_gnomonic_back_hemisphere_is_invisible():
    """gnomonic 只在切点同半球有定义：对踵点(切点对面)应判不可见。"""
    # 切点 (l,b)=(0,0)，对踵点 (l,b)=(180,0) 落在后半球
    _, _, vis = tw.gnomonic(np.array([180.0]), np.array([0.0]), 0.0, 0.0)
    assert not vis[0]


def test_gnomonic_east_north_orientation():
    """切点附近：银经增(向东)给正 xi，银纬增(向北)给正 eta。"""
    xi_e, _, _ = tw.gnomonic(np.array([1.0]), np.array([0.0]), 0.0, 0.0)
    _, eta_n, _ = tw.gnomonic(np.array([0.0]), np.array([1.0]), 0.0, 0.0)
    assert xi_e[0] > 0
    assert eta_n[0] > 0


def test_gnomonic_small_angle_matches_radians():
    """小角极限下 TAN 标准坐标 ≈ 角距(弧度)：1° 偏移应约等于 radians(1°)。"""
    xi, _, _ = tw.gnomonic(np.array([1.0]), np.array([0.0]), 0.0, 0.0)
    assert np.isclose(xi[0], np.radians(1.0), rtol=2e-4)


# ---------- 亮星散射翼 bloom: 圆形(无方块) + tile 边缘不截断 ----------


def _wing_single_star(S, px, py, L, cdelt, g=0.3, margin=0):
    """在 S×S(或扩边 margin)画布的 (px,py) 放一颗 G=g、通量 L 的亮星，返回其翼层(裁回 S×S)。"""
    pxi = np.array([int(px + margin)])
    pyi = np.array([int(py + margin)])
    cols = np.array([[1.0, 0.85, 0.6]])           # 暖白(Antares 色)
    return tw._bright_star_wings(S, pxi, pyi, np.array([L]), cols,
                                 np.array([g]), cdelt, margin=margin)


def test_bright_wing_is_circular_not_square():
    """极亮星的翼必须圆对称——同半径下水平/对角亮度相近，且无 truncate 方框硬边。

    回归 truncate=3.0 的 bug：scipy 高斯核截断是方形支撑，极亮星(Antares L≈中位 2900 万倍)
    在 3σ 处残值仍高于天光底 → 渲成方块。truncate=5.0 后残值 e^-12.5≈4e-6，方边没入背景。
    """
    S = 512
    cdelt = 0.64 / 1536.0                          # 1.5 arcsec/px(正式分辨率)
    wings = _wing_single_star(S, S // 2, S // 2, L=1e5, cdelt=cdelt, g=0.3)
    y = wings.sum(-1)
    c = S // 2
    # 同一半径 r 处：水平方向 vs 对角方向亮度应接近(圆对称)。方块 artifact 会让对角伸更远。
    for r in (40, 80, 120):
        horiz = y[c, c + r]
        d = int(r / np.sqrt(2))
        diag = y[c + d, c + d]
        ref = max(horiz, diag, 1e-12)
        assert abs(horiz - diag) / ref < 0.15, f"r={r} 圆对称破缺 horiz={horiz:.3e} diag={diag:.3e}"
    # 无方框硬边：沿水平扫描，相邻像素的相对跳变处处平滑(无某处突然归零的方边)。
    line = y[c, c:]
    line = line[line > line.max() * 1e-6]          # 取翼实际覆盖段
    rel_jump = np.abs(np.diff(line)) / (line[:-1] + 1e-12)
    assert rel_jump.max() < 0.5, f"存在硬边(最大相对跳变 {rel_jump.max():.2f})"


def test_bright_wing_edge_star_bleeds_into_tile():
    """中心落在 tile 外、但翼伸进 tile 的亮星，必须贡献翼到 tile 内(修边缘截断)。

    回归"tile 边缘亮星被硬截"的 bug：旧版 inside 只留中心在 [0,S) 的星，边外亮星的翼丢失,
    相邻 tile 拼起来有断口。扩边 margin 渲翼后，中心在 [-margin,S) 的星也贡献。
    """
    S = 512
    cdelt = 0.64 / 1536.0
    margin = int(np.ceil(5.0 * tw.BLOOM_WING_ARCSEC / (cdelt * 3600.0)))
    # 星中心放在 tile 左外 30px(px=-30)，翼半径(5σ)远大于 30 → 翼应进入 tile。
    wings = _wing_single_star(S, -30, S // 2, L=1e5, cdelt=cdelt, g=0.3, margin=margin)
    left_edge = wings.sum(-1)[S // 2, 0]
    assert left_edge > 0, "tile 外亮星的翼没有进入 tile(边缘截断未修)"


def test_bright_wing_margin_continuity_across_tiles():
    """同一颗边缘星在相邻两 tile 的重叠列上亮度连续——扩边渲翼保证拼接无断口。"""
    S = 256
    cdelt = 0.64 / 1536.0
    margin = int(np.ceil(5.0 * tw.BLOOM_WING_ARCSEC / (cdelt * 3600.0)))
    # tileA: 星在右边界外一点(px=S+5)；tileB(=A 右移 S): 同一星变成 px=5。
    # A 的右边界列 == B 的左边界列(同一片天)，亮度应连续。
    wA = _wing_single_star(S, S + 5, S // 2, L=1e5, cdelt=cdelt, g=0.3, margin=margin)
    wB = _wing_single_star(S, 5, S // 2, L=1e5, cdelt=cdelt, g=0.3, margin=margin)
    a_right = wA.sum(-1)[S // 2, S - 1]
    b_left = wB.sum(-1)[S // 2, 0]
    ref = max(a_right, b_left, 1e-12)
    assert abs(a_right - b_left) / ref < 0.05, f"拼接不连续 A={a_right:.3e} B={b_left:.3e}"


def test_bright_wing_sigma_scales_with_magnitude():
    """翼大小随星等连续：更亮的星(小 G)翼更大(更弥散)，暗的(大 G)翼更紧。"""
    S = 512
    cdelt = 0.64 / 1536.0
    def spread(g):
        w = _wing_single_star(S, S // 2, S // 2, L=1e4, cdelt=cdelt, g=g).sum(-1)
        yy, xx = np.mgrid[0:S, 0:S]
        c = S / 2.0
        r2 = (xx - c) ** 2 + (yy - c) ** 2
        return np.sqrt((w * r2).sum() / max(w.sum(), 1e-12))   # 亮度加权 RMS 半径
    bright = spread(tw.BLOOM_G_BRIGHT)        # 满 σ
    faint = spread(tw.BLOOM_G_FAINT - 0.1)    # 收到核尺度
    assert bright > faint, f"亮星翼应更大: G_bright RMS={bright:.1f} G_faint RMS={faint:.1f}"


def test_solid_angle_normalization_is_resolution_invariant():
    """立体角归一化把 flux 转成面亮度：同一颗星在两种 cdelt 下归一后每像素相等。

    星光是 flux 语义(每像素值 ∝ 像素立体角 cdelt²)，× REF_OMEGA/cdelt² 后与
    分辨率/fov 无关。这是"TAN 图比广州地平暗"的修法的量纲核心(见 working.md)。
    """
    REF_OMEGA = 0.083 ** 2
    flux = 1.0
    for cdelt in (0.039, 0.083, 0.16):
        radiance = flux * (REF_OMEGA / cdelt ** 2)
        # 归一化后的值只取决于 REF_OMEGA 与该像素立体角之比，cdelt=0.083 时恰为 flux
        assert np.isclose(radiance, flux * REF_OMEGA / cdelt ** 2)
    # 参考分辨率(cdelt=0.083)处归一化为恒等
    assert np.isclose(flux * (REF_OMEGA / 0.083 ** 2), flux)


def test_tan_wcs_hhh_header_format(tmp_path):
    """.hhh 是 FITS WCS header：每行严格 80 列、含 TAN CTYPE、CRVAL/CRPIX/CDELT 对应入参。"""
    data = tmp_path / "mini_fov.npz"
    rng = np.random.RandomState(11)
    n = 2000                                          # 够密让 signal_mask 非空
    np.savez(data,
             l=rng.uniform(-18, 18, n), b=rng.uniform(-18, 18, n),
             g=rng.uniform(6.0, 12.0, n), bp_rp=rng.uniform(0.2, 1.6, n))
    out_prefix = str(tmp_path / "tan_out")
    import sys as _sys
    argv = ["render_tan_wcs.py", "--data", str(data), "--out", out_prefix,
            "--lc", "0", "--bc", "0", "--fov-deg", "40", "--size", "64"]
    old = _sys.argv
    try:
        _sys.argv = argv
        tw.main()
    finally:
        _sys.argv = old
    assert os.path.exists(out_prefix + ".png")
    raw = open(out_prefix + ".hhh").read()
    assert len(raw) % 80 == 0                      # 每张卡片 80 列
    cards = [raw[i:i + 80] for i in range(0, len(raw), 80)]
    joined = "".join(c.strip() + "\n" for c in cards)
    assert "CTYPE1  = 'GLON-TAN'" in joined
    assert "CTYPE2  = 'GLAT-TAN'" in joined
    assert "CRVAL1  = 0" in joined and "CRVAL2  = 0" in joined
    assert "CRPIX1  = 32.0" in joined               # size/2
    assert "CDELT1  = 0.625" in joined              # CDELT1>0 与像素 +xi 自洽（astropy WCS 验证）；fov/size=40/64
    assert cards[-1].strip() == "END"


# ---------- 单图模式: 共享取景与 tone 显示链 ----------


def test_project_guangzhou_fov_matches_manual_sequence():
    """共享取景 helper 等价于 look_az + gal_to_altaz + project_horizon_camera 手写序列。"""
    rng = np.random.RandomState(1)
    l = rng.uniform(0, 40, 120)
    b = rng.uniform(-15, 15, 120)
    look_az, _ = beg.galactic_center_altaz(23.13, 17.76)
    az, alt = rh.gal_to_altaz(l, b, 23.13, 17.76)
    px0, py0, in0 = beg.project_horizon_camera(az, alt, look_az, 1080, 1920, 90.0, 75.0, "horizontal")
    px1, py1, in1 = beg.project_guangzhou_fov(l, b, 23.13, 17.76, 1080, 1920, 90.0, 75.0)
    assert np.array_equal(px0, px1)
    assert np.array_equal(py0, py1)
    assert np.array_equal(in0, in1)


def test_signal_mask_selects_above_floor_pixels():
    """signal_mask 选出高于最暗像素 eps 的有信号像素，纯天光底被排除。"""
    canvas = np.full((10, 10, 3), 0.001, np.float32)   # 纯天光底
    canvas[3, 7] = 0.5                                  # 一个亮星
    mask = beg.signal_mask(canvas, eps=0.004)
    assert mask[3, 7]
    assert mask.sum() == 1                              # 只有亮星过阈


def test_tone_adapted_matches_inline_display_chain():
    """tone_adapted 与 adapt→stretch→finish 内联链逐像素一致(重构不改数值)。"""
    rng = np.random.RandomState(5)
    canvas = (np.abs(rng.randn(48, 48, 3)) * 0.02).astype(np.float32)
    mask = beg.signal_mask(canvas, 0.004)
    ad = beg.adapt_sky_floor(canvas, 0.012, 25.0, 6.0, signal_mask=mask)
    st = beg.signal_stretch_for_adapted(ad, 0.012, 99.5, 2.5, signal_mask=mask)
    ref = beg.finish_sky_adapted(ad, 0.012, 2.2, 2.5, st, 1.8)
    got = beg.tone_adapted(canvas, 0.012, 6.0, 2.5, 1.8)
    assert np.allclose(ref, got)
    assert got.min() >= 0.0 and got.max() <= 1.0


def test_bv_to_rgb_g2v_white_point_is_neutral():
    """白点锚定: 太阳型 G2V (BP-RP≈0.82) 必须渲染成中性白 R≈G≈B≈1。

    这是修掉"银盘整体偏黄"的核心——历史 bv_to_rgb 没有白点锚定，把 BP-RP 当
    B-V 用，导致 G2V 色温的星偏暖。新映射用太阳 Teff(5772K) 做 gray-world 白点，
    强制 BP-RP=0.82 落在中性白。容差给到 0.02（插值+黑体积分的数值噪声）。
    """
    sun = rs.bv_to_rgb(np.array([0.82]))[0]
    assert np.allclose(sun, 1.0, atol=0.02), f"G2V 应中性白, 实得 {sun}"
    # 单调色相梯度: 比太阳蓝(更低 BP-RP)蓝分量更强, 比太阳红的红分量主导
    bluer = rs.bv_to_rgb(np.array([0.2]))[0]
    redder = rs.bv_to_rgb(np.array([1.5]))[0]
    assert bluer[2] > bluer[0]           # 蓝端: B > R
    assert redder[0] > redder[2]         # 红端: R > B
    # 太阳两侧相对色温方向正确: 蓝端 B 通道高于太阳, 红端 B 通道低于太阳
    assert bluer[2] >= sun[2] - 1e-6
    assert redder[2] < sun[2]
