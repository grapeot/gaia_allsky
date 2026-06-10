"""3D reproject 全天星图: 把观测者从太阳系挪到星际空间任意点, 看星空怎么变。

Gaia 视差→距离, 得每颗星 3D 笛卡尔坐标。平移观测者后:
- 近处星(视差大、距离准)位移明显 → 星座散架变形(真实)
- 远处星几乎不动(本来就远) → 银河带稳定(银河是大尺度结构, 非地球中心幻觉)
- 飞出银盘(垂直银道)→ 银河带从环绕变盘状(看见自己来自的星盘)

亮度按新距离平方反比修正(飞近变亮、飞远变暗, 真物理)。

L 型轨迹: 第一段沿银道(星座散架/银河不变), 第二段垂直银道(银河变盘)。
blooming: 亮星光晕外溢, 运镜中更扎眼、锁得住。
"""
import numpy as np
import render_starmap as rs


# 银道极方向(赤道坐标系下的银北极), 用于定义"沿银道/垂直银道"飞行方向
# 银北极 RA=192.86°, Dec=27.13° (J2000)
_NGP_RA, _NGP_DEC = 192.85948, 27.12825
# 银心方向 RA=266.405°, Dec=-28.936°
_GC_RA, _GC_DEC = 266.40499, -28.93617


def _radec_dist_to_xyz(ra_deg, dec_deg, dist_pc):
    """赤道坐标+距离 → 笛卡尔 (x,y,z) pc。太阳系在原点。"""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    x = dist_pc * np.cos(dec) * np.cos(ra)
    y = dist_pc * np.cos(dec) * np.sin(ra)
    z = dist_pc * np.sin(dec)
    return np.stack([x, y, z], axis=-1)


def _dir_unit(ra_deg, dec_deg):
    """赤道方向 → 单位向量。"""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def flight_direction(mode):
    """飞行方向单位向量。mode='galactic_plane'沿银道(朝银心), 'galactic_pole'垂直银道(朝银北极)。"""
    if mode == "galactic_plane":
        return _dir_unit(_GC_RA, _GC_DEC)
    elif mode == "galactic_pole":
        return _dir_unit(_NGP_RA, _NGP_DEC)
    raise ValueError(mode)


def reproject_from(xyz_star, g_mag, obs_pos, m_ref=8.0):
    """观测者在 obs_pos(pc) 看每颗星: 返回新 (ra,dec,vis_mag, rel_dist)。

    vis_mag: 视星等随距离平方反比变化(飞近变亮)。rel_dist 用于排序/裁剪。
    """
    rel = xyz_star - obs_pos[None, :]            # 星相对新观测者
    d_new = np.sqrt((rel ** 2).sum(-1))          # 新距离 pc
    d_old = np.sqrt((xyz_star ** 2).sum(-1))     # 原距离(太阳系)
    # 新视星等 = 原视星等 + 5·log10(d_new/d_old)  (平方反比→星等)
    vis_mag = g_mag + 5.0 * np.log10(np.maximum(d_new, 1e-6) / np.maximum(d_old, 1e-6))
    # 新方向 → ra/dec
    x, y, z = rel[:, 0], rel[:, 1], rel[:, 2]
    ra = np.degrees(np.arctan2(y, x)) % 360.0
    dec = np.degrees(np.arcsin(np.clip(z / np.maximum(d_new, 1e-9), -1, 1)))
    return ra, dec, vis_mag, d_new


def project_equirect_eq(ra_deg, dec_deg, W, H):
    """赤道 equirectangular: RA 0-360→x, Dec +90..-90→y。VR 球面贴图。"""
    px = (np.asarray(ra_deg, float) % 360.0 / 360.0 * W).astype(int)
    py = ((90.0 - np.asarray(dec_deg, float)) / 180.0 * H).astype(int)
    inside = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    return px, py, inside


def add_bloom(canvas, threshold_pct=99.0, sigma=8.0, strength=0.6):
    """亮星 blooming: 阈值提取亮星(超 threshold_pct 百分位的像素) → 大核高斯光晕 → 叠加。
    运镜中亮星更扎眼、锁得住。strength: 光晕叠加系数; sigma: 高斯核(像素)。

    注: 这是旧式加性辉光, 与统一 PSF 成像模型不兼容(会破坏视尺寸单调性), 现仅作
    回退/对照保留。正式视频路径用 unified_psf_image()。
    """
    from scipy.ndimage import gaussian_filter
    Y = canvas.sum(-1)
    thr = np.percentile(Y[Y > 0], threshold_pct) if (Y > 0).any() else 1.0
    bright = np.where(Y[..., None] > thr, canvas, 0.0)
    halo = np.zeros_like(canvas)
    for c in range(3):
        halo[..., c] = gaussian_filter(bright[..., c], sigma)
    return canvas + halo * strength


# ---- 统一 PSF 成像模型 (与静态图 render_bortle_eye_grid 同一物理模型) ----

# 饱和锚点的参考星等: 视星等(锚定在 m_ref=8.0)亮于此值的恒星会触发饱和溢出。
# 飞行视频没有 skyglow/Bortle/NELM, 不能像静态图那样把饱和线锚在 skyglow 上。
# 改用一个固定的参考星等亮度作锚点: sat_level = sat_over_ref × L(SAT_REF_MAG)。
# 这是个纯几何/物理量, 不依赖任何一帧的亮度直方图, 因此整段视频里饱和起点恒定,
# 观测者移动、恒星视星等随距离变化时, 饱和阈值不会逐帧抖动。
SAT_REF_MAG_DEFAULT = 6.0


def sat_level_from_ref_mag(sat_over_ref, sat_ref_mag=SAT_REF_MAG_DEFAULT, m_ref=8.0):
    """把"参考星等 + 倍数"换算成线性域饱和像素亮度阈值。

    sat_over_ref <= 0 表示禁用饱和溢出。返回 None 或浮点阈值。
    阈值 = sat_over_ref × L(sat_ref_mag), L 用与渲染一致的 m_ref 锚点。
    """
    if sat_over_ref is None or sat_over_ref <= 0:
        return None
    return float(sat_over_ref) * float(rs.mag_to_luminance(sat_ref_mag, m_ref))


def unified_psf_image(H, W, px, py, inside, vis_mag, g_mag, cols,
                      m_ref=8.0, gain=1.0, psf_core_px=1.1,
                      faint_gain=4.2, faint_mag_min=9.0,
                      sat_level=None, wing_sigmas=(3.0, 9.0),
                      wing_weights=(0.65, 0.35)):
    """统一 PSF + 暗星截断补偿 + 饱和溢出, 把恒星累加成线性画布。

    与静态图 accumulate_uniform_psf_stars 同一物理模型, 区别在于截断补偿增益按
    **星表 G 星等(g_mag, 恒星固有属性, 不随观测者移动)** 选星, 而亮度用 **重投影
    后的视星等(vis_mag)**。这样飞近/飞远改变恒星视亮度, 但"哪些星代理 G>11 不可分辨
    族群"这一身份保持不变——G 截断是星表属性, 不是视角属性。

    - 所有恒星共享 psf_core_px 高斯核(一次整幅卷积, 代价与星数无关)。
    - G >= faint_mag_min 的暗星亮度乘 faint_gain, 代理 G=11 星表截断丢失的积分光。
    - 线性亮度超过 sat_level 的能量按双高斯溢出翼能量守恒地散布(亮星变大盘面)。
    """
    import render_bortle_eye_grid as beg

    L = rs.mag_to_luminance(vis_mag, m_ref) * gain
    boosted = L.copy()
    faint = np.asarray(g_mag) >= faint_mag_min
    boosted[faint] *= faint_gain
    canvas = rs.accumulate_stars(H, W, px, py, inside, boosted, cols, psf_px=psf_core_px)
    if sat_level is None:
        return canvas
    return beg.saturate_and_bloom(canvas, sat_level, wing_sigmas, wing_weights)


def render_fisheye_lookdir(xyz_star, g_mag, bv, obs_pos, look_dir, S, fov_deg=170.0,
                           m_ref=8.0, gain=1.0, psf_core_px=1.1, faint_gain=4.2,
                           faint_mag_min=9.0, sat_level=None, wing_sigmas=(3.0, 9.0),
                           wing_weights=(0.65, 0.35)):
    """方位(鱼眼)投影: 从 obs_pos 朝 look_dir 看半边天 → (S,S,3) 圆盘画布。

    用于"飞出去回望": 往银北极飞、look_dir 朝银南极(脚下), 整个数据球收缩成脚下发光的球。
    那个球就是 Gaia 可见光视差能及的边界——银河真身在球外, 够不着。
    """
    ra, dec, vis_mag, d_new = reproject_from(xyz_star, g_mag, obs_pos, m_ref)
    r, dc = np.radians(ra), np.radians(dec)
    svec = np.stack([np.cos(dc) * np.cos(r), np.cos(dc) * np.sin(r), np.sin(dc)], -1)
    ld = look_dir / np.linalg.norm(look_dir)
    ang = np.degrees(np.arccos(np.clip(svec @ ld, -1, 1)))   # 与 look 方向夹角
    sel = ang < fov_deg / 2
    tmp = np.array([0, 0, 1.0]) if abs(ld[2]) < 0.9 else np.array([1.0, 0, 0])
    e1 = np.cross(ld, tmp); e1 /= np.linalg.norm(e1)
    e2 = np.cross(ld, e1)
    u, v = svec @ e1, svec @ e2
    rr = ang / (fov_deg / 2)            # 归一化半径 0(中心)..1(边缘)
    th = np.arctan2(v, u)              # 盘内方位角(e1/e2 基; 旋转方向不影响"收缩成球"语义)
    px = ((rr * np.cos(th) * 0.5 + 0.5) * S).astype(int)
    py = ((rr * np.sin(th) * 0.5 + 0.5) * S).astype(int)
    ins = sel & (px >= 0) & (px < S) & (py >= 0) & (py < S)
    cols = rs.bv_to_rgb(bv)
    return unified_psf_image(
        S, S, px, py, ins, vis_mag, g_mag, cols, m_ref, gain,
        psf_core_px, faint_gain, faint_mag_min, sat_level, wing_sigmas, wing_weights,
    )


def render_perspective_lookdir(xyz_star, g_mag, bv, obs_pos, look_dir, W, H, fov_deg=90.0,
                               up_hint=None, m_ref=8.0, gain=1.0, psf_core_px=1.1,
                               faint_gain=4.2, faint_mag_min=9.0, sat_level=None,
                               wing_sigmas=(3.0, 9.0), wing_weights=(0.65, 0.35)):
    """Rectilinear forward camera looking along look_dir, filling the full WxH frame."""
    rel = xyz_star - obs_pos[None, :]
    d_new = np.sqrt((rel ** 2).sum(-1))
    d_old = np.sqrt((xyz_star ** 2).sum(-1))
    svec = rel / np.maximum(d_new[:, None], 1e-9)
    vis_mag = g_mag + 5.0 * np.log10(np.maximum(d_new, 1e-6) / np.maximum(d_old, 1e-6))

    forward = look_dir / np.linalg.norm(look_dir)
    up_hint = np.asarray(up_hint if up_hint is not None else [0.0, 0.0, 1.0], dtype=float)
    if abs(np.dot(forward, up_hint) / max(np.linalg.norm(up_hint), 1e-9)) > 0.95:
        up_hint = np.array([1.0, 0.0, 0.0])
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)

    z = svec @ forward
    x = svec @ right
    y = svec @ up
    tan_half = np.tan(np.radians(fov_deg) / 2.0)
    aspect = W / H
    nx = x / np.maximum(z, 1e-9) / (tan_half * aspect)
    ny = y / np.maximum(z, 1e-9) / tan_half
    px = ((nx * 0.5 + 0.5) * W).astype(int)
    py = ((0.5 - ny * 0.5) * H).astype(int)
    inside = (z > 0) & (np.abs(nx) <= 1) & (np.abs(ny) <= 1) & (px >= 0) & (px < W) & (py >= 0) & (py < H)
    cols = rs.bv_to_rgb(bv)
    return unified_psf_image(
        H, W, px, py, inside, vis_mag, g_mag, cols, m_ref, gain,
        psf_core_px, faint_gain, faint_mag_min, sat_level, wing_sigmas, wing_weights,
    )


def render_3d_frame(xyz_star, g_mag, bv, obs_pos, W, H, m_ref=8.0, gain=1.0,
                    psf_core_px=1.1, faint_gain=4.2, faint_mag_min=9.0, sat_level=None,
                    wing_sigmas=(3.0, 9.0), wing_weights=(0.65, 0.35)):
    """渲一帧 3D reproject 全天图(赤道 equirectangular) → (H,W,3) 线性画布。"""
    ra, dec, vis_mag, d_new = reproject_from(xyz_star, g_mag, obs_pos, m_ref)
    cols = rs.bv_to_rgb(bv)
    px, py, inside = project_equirect_eq(ra, dec, W, H)
    return unified_psf_image(
        H, W, px, py, inside, vis_mag, g_mag, cols, m_ref, gain,
        psf_core_px, faint_gain, faint_mag_min, sat_level, wing_sigmas, wing_weights,
    )


def l_trajectory(n_frames, leg1_pc, leg2_pc, hold_frac=0.1):
    """L 型飞行轨迹 → (n_frames,3) 观测者位置序列(pc)。

    第一段: 从原点沿银道方向飞 leg1_pc(星座散架/银河不变)。
    第二段: 转垂直银道方向飞 leg2_pc(银河变盘)。
    hold_frac: 首尾各停顿比例(让观众看清起止)。
    """
    d1 = flight_direction("galactic_plane")
    d2 = flight_direction("galactic_pole")
    n_hold = int(n_frames * hold_frac)
    n_move = n_frames - 2 * n_hold
    n1 = n_move // 2
    n2 = n_move - n1
    # ease-in-out 平滑(避免匀速生硬)
    def ease(t):
        return 0.5 - 0.5 * np.cos(np.pi * t)
    seg1 = np.array([ease(i / max(n1 - 1, 1)) * leg1_pc for i in range(n1)])
    end1 = seg1[-1] if n1 else 0.0
    seg2 = np.array([ease(i / max(n2 - 1, 1)) * leg2_pc for i in range(n2)])
    positions = []
    for _ in range(n_hold):
        positions.append(np.zeros(3))
    for s in seg1:
        positions.append(d1 * s)
    base = d1 * end1
    for s in seg2:
        positions.append(base + d2 * s)
    last = positions[-1].copy()
    for _ in range(n_hold):
        positions.append(last)
    return np.array(positions[:n_frames])
