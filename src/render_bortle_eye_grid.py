"""Render Bortle skyglow x eye-sensitivity comparison grids."""
import argparse
import os

import numpy as np

import render_horizon as rh
import render_starmap as rs


DATA_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_g13_render.npz")
OUTPUT_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "outputs", "knob_bortle_eye_grid.png")


BORTLE_NELM = {
    1: 7.8,
    2: 7.3,
    3: 6.8,
    4: 6.3,
    5: 5.8,
    6: 5.3,
    7: 4.8,
    8: 4.3,
    9: 4.0,
}


def parse_csv_numbers(text, cast=float):
    return [cast(x.strip()) for x in text.split(",") if x.strip()]


def gain_for_nelm(nelm, base_nelm=6.0):
    """Eye sensitivity gain relative to a NELM 6 naked-eye baseline."""
    return float(10.0 ** (0.4 * (nelm - base_nelm)))


def gain_for_mag_delta(delta_mag):
    """Sensitivity/exposure gain for a limiting-magnitude improvement."""
    return float(10.0 ** (0.4 * delta_mag))


def empirical_nelm_for_bortle(bortle):
    return BORTLE_NELM[int(round(bortle))]


def effective_nelm_for_panel(bortle, delta_mag):
    return empirical_nelm_for_bortle(bortle) + float(delta_mag)


def sky_limited_snr(star_signal, sky_signal, exposure=1.0, read_noise=0.0):
    """Source SNR under Poisson sky background.

    Longer exposure raises source signal linearly, but noise grows with the square root
    of source + sky + read-noise variance. Bright sky therefore needs much more
    exposure for the same SNR.
    """
    source = np.asarray(star_signal, dtype=float) * exposure
    sky = np.asarray(sky_signal, dtype=float) * exposure
    return source / np.sqrt(np.maximum(source + sky + read_noise ** 2, 1e-12))


def limiting_mag_for_sky(bortle, gain=1.0, snr_threshold=5.0, m_ref=8.0):
    """Approximate limiting magnitude implied by sky background and sensitivity gain."""
    sky = rh.skyglow_level(bortle, m_ref=m_ref)
    lo, hi = -2.0, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        star = rs.mag_to_luminance(mid, m_ref)
        snr = sky_limited_snr(star, sky, gain)
        if snr >= snr_threshold:
            lo = mid
        else:
            hi = mid
    return lo


def visual_luminance_for_mags(mag, bortle, delta_mag, limiting_contrast=0.5):
    """Star luminance anchored to empirical Bortle NELM.

    A star at the effective limiting magnitude is rendered as a fixed fraction of
    the current skyglow. This ties the visual model to observed naked-eye limits
    instead of arbitrary gain.
    """
    m_lim = effective_nelm_for_panel(bortle, delta_mag)
    sky = rh.skyglow_level(bortle)
    return sky * limiting_contrast * rs.mag_to_luminance(mag, m_lim)


def visual_luminance_for_mag(mag, bortle, delta_mag, limiting_contrast=0.5):
    return float(visual_luminance_for_mags(np.array([mag]), bortle, delta_mag, limiting_contrast)[0])


def add_skyglow(canvas, bortle):
    return canvas + rh.skyglow_level(bortle)


def saturate_and_bloom(canvas, sat_level, wing_sigmas=(3.0, 9.0), wing_weights=(0.65, 0.35)):
    """Linear-domain saturation overflow: clip energy above sat_level, scatter it wide.

    A real optical system has one PSF for every star; bright stars look bigger
    because tone-curve saturation widens the visible part of the same profile and
    scattering wings spread the rest. Redistributing the clipped excess through
    wide Gaussians keeps total energy and makes apparent star size grow
    continuously with brightness, with no segmentation seams.
    """
    from scipy.ndimage import gaussian_filter

    y = canvas.sum(axis=-1)
    over = y > sat_level
    if not np.any(over):
        return canvas
    scale = np.ones_like(y)
    scale[over] = sat_level / y[over]
    core = canvas * scale[:, :, None]
    excess = canvas - core
    wings = np.zeros_like(canvas)
    for sigma, weight in zip(wing_sigmas, wing_weights):
        for c in range(3):
            wings[..., c] += gaussian_filter(excess[..., c], sigma) * weight
    return core + wings


def accumulate_uniform_psf_stars(height, width, px, py, inside, mag, luminance, cols,
                                 psf_core_px=1.1, faint_gain=3.8, faint_mag_min=11.0,
                                 sat_level=None, wing_sigmas=(3.0, 9.0),
                                 wing_weights=(0.65, 0.35), proxy_atten=None):
    """Official star accumulation: one shared PSF for all stars.

    Faint stars at the catalog edge (G >= faint_mag_min) are multiplied by
    faint_gain to stand in for the integrated light lost to the catalog
    truncation (for the official G<13 cache, extrapolating the measured
    luminosity function puts the missing G=13-21 flux at ~2.8x the G=11-13
    bin, hence the default gain 3.8). The PSF is one whole-canvas Gaussian,
    so its cost is independent of star count. Saturation overflow then widens
    only the brightest stars.
    """
    boosted = luminance.copy()
    faint = mag >= faint_mag_min
    if proxy_atten is None:
        boosted[faint] *= faint_gain
    else:
        # 增益拆成「直接光 1 + 推断光 (gain-1)」，推断光按该星身后的
        # 全柱消光衰减(见 build_render_cache.py)。观测到的光永不衰减。
        boosted[faint] *= 1.0 + (faint_gain - 1.0) * proxy_atten[faint]
    canvas = rs.accumulate_stars(height, width, px, py, inside, boosted, cols, psf_px=psf_core_px)
    if sat_level is None:
        return canvas
    return saturate_and_bloom(canvas, sat_level, wing_sigmas, wing_weights)


def apply_extended_visibility_threshold(canvas, sky, threshold=0.035, sigma_px=8.0):
    """Weber-type contrast threshold for extended light.

    The eye detects point sources and extended surface brightness with very
    different thresholds: diffuse structure below a few percent of the sky
    background is invisible to the eye even though a camera records it. This is
    why the Milky Way disappears around Bortle 7 while a tracked exposure still
    picks it up. Split the star canvas by spatial frequency and subtract
    threshold*sky from the low-frequency (extended) component only; point stars
    live in the high-frequency component and keep their NELM-anchored contrast.
    """
    if not threshold or threshold <= 0:
        return canvas
    from scipy.ndimage import gaussian_filter

    y = canvas.sum(axis=-1)
    low = gaussian_filter(y, sigma_px)
    visible_low = np.maximum(low - threshold * sky, 0.0)
    new_y = (y - low) + visible_low
    scale = np.clip(new_y / np.maximum(y, 1e-12), 0.0, None)
    return canvas * scale[:, :, None]


def adapt_sky_floor(canvas, target_sky=0.03, sky_pct=25.0, star_contrast=4.0):
    y = canvas.sum(axis=-1)
    sky_level = float(np.percentile(y, sky_pct))
    scale = target_sky / max(sky_level, 1e-9)
    adapted = canvas * scale
    sky_rgb = target_sky / 3.0
    return sky_rgb + np.maximum(adapted - sky_rgb, 0.0) * star_contrast


def signal_stretch_for_adapted(adapted, target_sky=0.03, white_pct=99.5, target_white=3.0):
    y = adapted.sum(axis=-1)
    white = max(float(np.percentile(y, white_pct)), target_sky + 1e-9)
    return max((target_white - target_sky) / max(white - target_sky, 1e-9), 1.0)


def finish_sky_adapted(adapted, target_sky=0.03, gamma=2.2, target_white=3.0, signal_stretch=1.0):
    """共享 stretch 后做 gamma 输出，高光用软肩滚降而不是硬截断。

    旧版把 y > target_white 的像素整体压到 target_white，银心这类成片高光
    会变成无纹理的平台（clip 感）。G<11 时代只影响零散像素；G<13 的细腻
    乳光让平台连成片，必须改成软肩：y 在 target_white 以上平滑滚向显示
    上限 3.0（RGB 和的最大值），膝点处导数为 1，高光内部保持单调有纹理。
    """
    sky_rgb = target_sky / 3.0
    adapted = sky_rgb + np.maximum(adapted - sky_rgb, 0.0) * signal_stretch
    y = adapted.sum(axis=-1)
    y_max = 3.0
    headroom = max(y_max - target_white, 1e-9)
    over = y > target_white
    if np.any(over):
        adapted = adapted.copy()
        y_over = y[over]
        y_new = target_white + headroom * (1.0 - np.exp(-(y_over - target_white) / headroom))
        adapted[over] *= (y_new / np.maximum(y_over, 1e-9))[:, None]
    return np.clip(adapted, 0, 1) ** (1 / gamma)


def normalize_sky_adapted(canvas, target_sky=0.03, gamma=2.2, white_pct=99.5, sky_pct=25.0,
                          star_contrast=4.0, target_white=3.0, signal_stretch=None):
    """Normalize like eye/camera adaptation: stable sky floor, stretched signal."""
    adapted = adapt_sky_floor(canvas, target_sky, sky_pct, star_contrast)
    if target_white is None:
        return np.clip(adapted, 0, 1) ** (1 / gamma)
    if signal_stretch is None:
        signal_stretch = signal_stretch_for_adapted(adapted, target_sky, white_pct, target_white)
    return finish_sky_adapted(adapted, target_sky, gamma, target_white, signal_stretch)


def normalize_panel(canvas, mode, pct, gamma, target_sky, white_pct, sky_pct, star_contrast, target_white,
                    signal_stretch=None):
    if mode == "sky_median":
        return (normalize_sky_adapted(canvas, target_sky, gamma, white_pct, sky_pct, star_contrast, target_white, signal_stretch) * 255).astype(np.uint8)
    return (rs.normalize_brightness(canvas, pct, "gamma", gamma) * 255).astype(np.uint8)


def altaz_to_local_vec(az_deg, alt_deg):
    """Az/alt to local ENU-like unit vectors: x=east, y=north, z=up."""
    az = np.radians(az_deg)
    alt = np.radians(alt_deg)
    return np.stack([
        np.cos(alt) * np.sin(az),
        np.cos(alt) * np.cos(az),
        np.sin(alt),
    ], axis=-1)


def project_perspective_altaz(az, alt, look_az, look_alt, width, height, fov_deg):
    svec = altaz_to_local_vec(az, alt)
    forward = altaz_to_local_vec(np.array([look_az]), np.array([look_alt]))[0]
    up_hint = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(forward, up_hint)) > 0.95:
        up_hint = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    z = svec @ forward
    x = svec @ right
    y = svec @ up
    tan_half = np.tan(np.radians(fov_deg) / 2.0)
    aspect = width / height
    nx = x / np.maximum(z, 1e-9) / (tan_half * aspect)
    ny = y / np.maximum(z, 1e-9) / tan_half
    px = ((nx * 0.5 + 0.5) * width).astype(int)
    py = ((0.5 - ny * 0.5) * height).astype(int)
    inside = (alt > 0) & (z > 0) & (np.abs(nx) <= 1) & (np.abs(ny) <= 1) & (px >= 0) & (px < width) & (py >= 0) & (py < height)
    return px, py, inside


def angular_delta_deg(angle, center):
    return (np.asarray(angle, float) - center + 180.0) % 360.0 - 180.0


def project_horizon_window(az, alt, center_az, width, height, az_width_deg, max_alt_deg):
    """Human sky window: bottom edge is horizon, y increases upward in altitude."""
    dx = angular_delta_deg(az, center_az)
    px = np.clip(((dx / az_width_deg + 0.5) * width).astype(int), 0, width - 1)
    py = np.clip(((1.0 - np.asarray(alt, float) / max_alt_deg) * height).astype(int), 0, height - 1)
    inside = (
        (np.abs(dx) <= az_width_deg / 2)
        & (alt >= 0)
        & (alt <= max_alt_deg)
        & (px >= 0)
        & (px < width)
        & (py >= 0)
        & (py < height)
    )
    return px, py, inside


def aspect_preserving_horizon_fovs(width, height, h_fov_deg, v_fov_deg, fov_axis="horizontal"):
    """Return rectilinear FOVs that match the image aspect ratio.

    A real rectilinear camera cannot choose horizontal and vertical FOV
    independently for a fixed sensor aspect. Doing so squeezes the sky.
    """
    aspect = width / height
    if fov_axis == "vertical":
        tan_v = np.tan(np.radians(v_fov_deg) / 2.0)
        h_fov_deg = np.degrees(2.0 * np.arctan(tan_v * aspect))
    else:
        tan_h = np.tan(np.radians(h_fov_deg) / 2.0)
        v_fov_deg = np.degrees(2.0 * np.arctan(tan_h / aspect))
    return float(h_fov_deg), float(v_fov_deg)


def project_horizon_camera(az, alt, center_az, width, height, h_fov_deg, v_fov_deg,
                           fov_axis="horizontal"):
    """Rectilinear camera with the horizon crossing the bottom-center pixel."""
    h_fov_deg, v_fov_deg = aspect_preserving_horizon_fovs(
        width, height, h_fov_deg, v_fov_deg, fov_axis
    )
    look_alt = v_fov_deg / 2.0
    svec = altaz_to_local_vec(az, alt)
    forward = altaz_to_local_vec(np.array([center_az]), np.array([look_alt]))[0]
    up_hint = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    z = svec @ forward
    x = svec @ right
    y = svec @ up
    nx = x / np.maximum(z, 1e-9) / np.tan(np.radians(h_fov_deg) / 2.0)
    ny = y / np.maximum(z, 1e-9) / np.tan(np.radians(v_fov_deg) / 2.0)
    px = np.clip(((nx * 0.5 + 0.5) * width).astype(int), 0, width - 1)
    py = np.clip(((0.5 - ny * 0.5) * height).astype(int), 0, height - 1)
    inside = (
        (alt >= 0)
        & (z > 0)
        & (np.abs(nx) <= 1)
        & (np.abs(ny) <= 1)
        & (px >= 0)
        & (px < width)
        & (py >= 0)
        & (py < height)
    )
    return px, py, inside


def galactic_center_altaz(lat_deg, lst_hours):
    az, alt = rh.gal_to_altaz(np.array([0.0]), np.array([0.0]), lat_deg, lst_hours)
    return float(az[0]), float(alt[0])


def label_panel(img, text):
    from PIL import Image, ImageDraw, ImageFont

    out = Image.fromarray(img)
    draw = ImageDraw.Draw(out, "RGBA")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    pad = 10
    box = draw.textbbox((pad, pad), text, font=font)
    draw.rectangle((box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4), fill=(0, 0, 0, 150))
    draw.text((pad, pad), text, fill=(255, 255, 255, 230), font=font)
    return np.asarray(out)


def render_panel_canvas(l, b, g, bv, bortle, value, width, height, lat_deg, lst_hours,
                        projection, look_az, look_alt, fov_deg, az_width_deg, max_alt_deg,
                        limiting_contrast, psf_core_px, faint_gain, faint_mag_min,
                        sat_over_sky, wing_sigmas, wing_weights, mode,
                        fov_axis="horizontal", ext_threshold=0.035, ext_sigma=8.0,
                        proxy_atten=None):
    az, alt = rh.gal_to_altaz(l, b, lat_deg, lst_hours)
    cols = rs.bv_to_rgb(bv)
    if projection == "equirect":
        px, py, inside = rh.project_horizon_equirect(az, alt, width, height)
    elif projection == "horizon_window":
        px, py, inside = project_horizon_camera(
            az, alt, look_az, width, height, az_width_deg, max_alt_deg, fov_axis
        )
    else:
        px, py, inside = project_perspective_altaz(az, alt, look_az, look_alt, width, height, fov_deg)

    if mode == "snr":
        L = rs.mag_to_luminance(g, 8.0)
        L = sky_limited_snr(L, rh.skyglow_level(bortle), float(value))
        return rs.accumulate_stars(height, width, px, py, inside, L, cols, psf_px=1.0)

    L = visual_luminance_for_mags(g, bortle, value, limiting_contrast)
    sky = rh.skyglow_level(bortle)
    # Saturation rides the same magnitude ladder as star luminance: a +delta_mag
    # panel scales every star by 10^(0.4*delta), so the threshold must scale too,
    # keeping saturation onset at a fixed magnitude depth below the effective limit.
    # A sky-anchored constant would clip whole Milky-Way regions at high delta.
    sat_level = None
    if sat_over_sky and sat_over_sky > 0:
        sat_level = sat_over_sky * sky * gain_for_mag_delta(float(value))
    canvas = accumulate_uniform_psf_stars(
        height, width, px, py, inside, g, L, cols,
        psf_core_px, faint_gain, faint_mag_min, sat_level, wing_sigmas, wing_weights,
        proxy_atten,
    )
    canvas = apply_extended_visibility_threshold(canvas, sky, ext_threshold, ext_sigma)
    return add_skyglow(canvas, bortle)


def render_grid(data_path, output, bortles, values, panel_width, panel_height, lat_deg, lst_hours,
                pct, gamma, projection, look_az, look_alt, fov_deg, normalization, target_sky,
                white_pct, sky_pct, star_contrast, target_white, limiting_contrast, az_width_deg,
                max_alt_deg, psf_core_px, faint_gain, faint_mag_min, reference_mode,
                reference_bortle, reference_value, mode, columns_per_row=None,
                sat_over_sky=6.0, wing_sigmas=(3.0, 9.0), wing_weights=(0.65, 0.35),
                fov_axis="horizontal", ext_threshold=0.035, ext_sigma=8.0):
    from PIL import Image

    d = np.load(data_path)
    l, b, g = d["l"], d["b"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    proxy_atten = d["proxy_atten"] if "proxy_atten" in d.files else None
    panels_flat = []
    default_az, default_alt = None, None
    if look_az is None or (projection == "perspective" and look_alt is None):
        default_az, default_alt = galactic_center_altaz(lat_deg, lst_hours)
    if look_az is None:
        look_az = default_az
    if look_alt is None:
        look_alt = default_alt
    signal_stretch = None
    if mode != "snr" and normalization == "sky_median" and target_white is not None:
        ref_bortle, ref_value = bortles[0], values[0]
        if reference_bortle is not None:
            ref_bortle = reference_bortle
        if reference_value is not None:
            ref_value = reference_value
        if reference_bortle is None and reference_value is None and reference_mode == "brightest":
            best_white = -np.inf
            for b_ref in bortles:
                for v_ref in values:
                    candidate = render_panel_canvas(
                        l, b, g, bv, b_ref, v_ref, panel_width, panel_height, lat_deg, lst_hours,
                        projection, look_az, look_alt, fov_deg, az_width_deg, max_alt_deg,
                        limiting_contrast, psf_core_px, faint_gain, faint_mag_min,
                        sat_over_sky, wing_sigmas, wing_weights, mode, fov_axis,
                        ext_threshold, ext_sigma, proxy_atten,
                    )
                    adapted = adapt_sky_floor(candidate, target_sky, sky_pct, star_contrast)
                    white = float(np.percentile(adapted.sum(axis=-1), white_pct))
                    if white > best_white:
                        best_white = white
                        ref_bortle, ref_value = b_ref, v_ref
        ref_canvas = render_panel_canvas(
            l, b, g, bv, ref_bortle, ref_value, panel_width, panel_height, lat_deg, lst_hours,
            projection, look_az, look_alt, fov_deg, az_width_deg, max_alt_deg,
            limiting_contrast, psf_core_px, faint_gain, faint_mag_min,
            sat_over_sky, wing_sigmas, wing_weights, mode, fov_axis,
            ext_threshold, ext_sigma, proxy_atten,
        )
        ref_adapted = adapt_sky_floor(ref_canvas, target_sky, sky_pct, star_contrast)
        signal_stretch = signal_stretch_for_adapted(ref_adapted, target_sky, white_pct, target_white)
    for bortle in bortles:
        for value in values:
            canvas = render_panel_canvas(
                l, b, g, bv, bortle, value, panel_width, panel_height, lat_deg, lst_hours,
                projection, look_az, look_alt, fov_deg, az_width_deg, max_alt_deg,
                limiting_contrast, psf_core_px, faint_gain, faint_mag_min,
                sat_over_sky, wing_sigmas, wing_weights, mode, fov_axis,
                ext_threshold, ext_sigma, proxy_atten,
            )
            panel = normalize_panel(
                canvas, normalization, pct, gamma, target_sky, white_pct, sky_pct,
                star_contrast, target_white, signal_stretch,
            )
            if projection == "equirect":
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  equirect"
            elif projection == "horizon_window":
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  Guangzhou horizon"
            else:
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  Guangzhou wide"
            panels_flat.append(label_panel(panel, label))
    columns = columns_per_row or len(values)
    rows = []
    blank = np.zeros_like(panels_flat[0])
    for i in range(0, len(panels_flat), columns):
        chunk = panels_flat[i:i + columns]
        while len(chunk) < columns:
            chunk.append(blank)
        rows.append(np.concatenate(chunk, axis=1))
    grid = np.concatenate(rows, axis=0)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    Image.fromarray(grid).save(output)
    return output


def column_label(mode, value, bortle=None):
    if mode == "snr":
        return f"exp {value:g}x"
    if bortle is None:
        return f"cost +{value:g}mag"
    nelm = effective_nelm_for_panel(bortle, value)
    return f"cost +{value:g}mag  NELM~{nelm:.1f}"


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=DATA_DEFAULT)
    p.add_argument("--output", default=OUTPUT_DEFAULT)
    p.add_argument("--bortles", default="1,6")
    p.add_argument("--eye-deltas", default="0,2,4", help="Sensitivity-cost columns in magnitudes for adapted visual mode.")
    p.add_argument("--exposures", default="1,10,100", help="Exposure multiplier columns for SNR mode.")
    p.add_argument("--panel-width", type=int, default=1080)
    p.add_argument("--panel-height", type=int, default=1920)
    p.add_argument("--lat-deg", type=float, default=23.13)
    p.add_argument("--lst-hours", type=float, default=17.76, help="LST near galactic-center culmination.")
    p.add_argument("--projection", choices=["horizon_window", "perspective", "equirect"], default="horizon_window")
    p.add_argument("--look-az", type=float)
    p.add_argument("--look-alt", type=float)
    p.add_argument("--fov-deg", type=float, default=110.0)
    p.add_argument("--az-width-deg", type=float, default=90.0, help="Horizontal FOV for horizon_window camera.")
    p.add_argument("--max-alt-deg", type=float, default=75.0,
                   help="Vertical FOV reference for horizon_window camera when --fov-axis vertical.")
    p.add_argument("--fov-axis", choices=["horizontal", "vertical"], default="horizontal",
                   help="Primary FOV axis for horizon_window; the other axis is derived from image aspect ratio.")
    p.add_argument("--normalization", choices=["sky_median", "percentile"], default="sky_median")
    p.add_argument("--target-sky", type=float, default=0.03,
                   help="Linear sky RGB-channel level after adaptation normalization.")
    p.add_argument("--sky-pct", type=float, default=25.0,
                   help="Low percentile used as background sky estimate for adaptation.")
    p.add_argument("--star-contrast", type=float, default=4.0,
                   help="Contrast boost for signal above the adapted sky background.")
    p.add_argument("--target-white", type=float, default=2.0,
                   help="Linear RGB-sum target for the white percentile after sky adaptation.")
    p.add_argument("--limiting-contrast", type=float, default=0.5,
                   help="Linear star/sky contrast for a star at the empirical limiting magnitude.")
    p.add_argument("--psf-core-px", type=float, default=1.1,
                   help="Shared Gaussian PSF sigma in pixels applied to every star.")
    p.add_argument("--faint-gain", type=float, default=3.8,
                   help="Luminance gain for stars at G >= faint-mag-min, standing in for the "
                        "integrated light lost to the G=13 catalog truncation.")
    p.add_argument("--faint-mag-min", type=float, default=11.0,
                   help="Magnitude threshold above which the catalog-truncation gain applies.")
    p.add_argument("--sat-over-sky", type=float, default=6.0,
                   help="Linear saturation level as a multiple of skyglow at +0mag; it scales "
                        "with the eye-delta gain so saturation starts at a fixed magnitude depth "
                        "below the effective limit. Energy above it is redistributed into wide "
                        "scattering wings. <=0 disables saturation bloom.")
    p.add_argument("--wing-sigmas", default="3,9",
                   help="Gaussian sigmas (px, CSV) for the saturation scattering wings.")
    p.add_argument("--wing-weights", default="0.65,0.35",
                   help="Energy weights (CSV) for the saturation scattering wings.")
    p.add_argument("--ext-threshold", type=float, default=0.035,
                   help="Weber contrast threshold for extended light as a fraction of skyglow; "
                        "diffuse structure below it is invisible to the eye. <=0 disables.")
    p.add_argument("--ext-sigma", type=float, default=8.0,
                   help="Gaussian sigma in pixels separating extended glow from point stars "
                        "for the visibility threshold.")
    p.add_argument("--reference-mode", choices=["brightest", "first"], default="brightest",
                   help="Panel used to calibrate shared visual stretch for the whole grid.")
    p.add_argument("--reference-bortle", type=int,
                   help="Explicit Bortle value for shared visual stretch reference.")
    p.add_argument("--reference-value", type=float,
                   help="Explicit eye delta/exposure value for shared visual stretch reference.")
    p.add_argument("--white-pct", type=float, default=99.5,
                   help="Highlight percentile mapped to white after sky adaptation.")
    p.add_argument("--mode", choices=["adapted", "snr"], default="adapted",
                    help="adapted: official visual sensitivity-cost grid; snr: debug sky-limited exposure model.")
    p.add_argument("--columns-per-row", type=int, help="Wrap panels into a fixed number of columns.")
    p.add_argument("--pct", type=float, default=99.7)
    p.add_argument("--gamma", type=float, default=2.2)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    values = parse_csv_numbers(args.exposures if args.mode == "snr" else args.eye_deltas, float)
    out = render_grid(
        args.data,
        args.output,
        parse_csv_numbers(args.bortles, int),
        values,
        args.panel_width,
        args.panel_height,
        args.lat_deg,
        args.lst_hours,
        args.pct,
        args.gamma,
        args.projection,
        args.look_az,
        args.look_alt,
        args.fov_deg,
        args.normalization,
        args.target_sky,
        args.white_pct,
        args.sky_pct,
        args.star_contrast,
        args.target_white,
        args.limiting_contrast,
        args.az_width_deg,
        args.max_alt_deg,
        args.psf_core_px,
        args.faint_gain,
        args.faint_mag_min,
        args.reference_mode,
        args.reference_bortle,
        args.reference_value,
        args.mode,
        args.columns_per_row,
        args.sat_over_sky,
        tuple(parse_csv_numbers(args.wing_sigmas, float)),
        tuple(parse_csv_numbers(args.wing_weights, float)),
        args.fov_axis,
        args.ext_threshold,
        args.ext_sigma,
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
