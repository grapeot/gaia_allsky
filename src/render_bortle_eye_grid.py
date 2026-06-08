"""Render Bortle skyglow x eye-sensitivity comparison grids."""
import argparse
import os

import numpy as np

import render_horizon as rh
import render_starmap as rs


DATA_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_g11.npz")
OUTPUT_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "outputs", "knob_bortle_eye_grid.png")


def parse_csv_numbers(text, cast=float):
    return [cast(x.strip()) for x in text.split(",") if x.strip()]


def gain_for_nelm(nelm, base_nelm=6.0):
    """Eye sensitivity gain relative to a NELM 6 naked-eye baseline."""
    return float(10.0 ** (0.4 * (nelm - base_nelm)))


def gain_for_mag_delta(delta_mag):
    """Sensitivity/exposure gain for a limiting-magnitude improvement."""
    return float(10.0 ** (0.4 * delta_mag))


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


def add_skyglow(canvas, bortle):
    return canvas + rh.skyglow_level(bortle)


def normalize_sky_adapted(canvas, target_sky=0.03, gamma=2.2, white_pct=99.5, sky_pct=25.0,
                          star_contrast=4.0):
    """Normalize like eye/camera adaptation: median sky stable, highlights compressed."""
    y = canvas.sum(axis=-1)
    sky_level = float(np.percentile(y, sky_pct))
    scale = target_sky / max(sky_level, 1e-9)
    adapted = canvas * scale
    sky_rgb = target_sky / 3.0
    adapted = sky_rgb + np.maximum(adapted - sky_rgb, 0.0) * star_contrast
    # Keep the adapted sky median fixed. Only compress highlights above a high
    # percentile; do not renormalize the whole image by the white point.
    y = adapted.sum(axis=-1)
    white = max(float(np.percentile(y, white_pct)), target_sky * 3.0, 1e-9)
    over = y > white
    if np.any(over):
        adapted = adapted.copy()
        adapted[over] *= (white / np.maximum(y[over], 1e-9))[:, None]
    return np.clip(adapted, 0, 1) ** (1 / gamma)


def normalize_panel(canvas, mode, pct, gamma, target_sky, white_pct, sky_pct, star_contrast):
    if mode == "sky_median":
        return (normalize_sky_adapted(canvas, target_sky, gamma, white_pct, sky_pct, star_contrast) * 255).astype(np.uint8)
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


def project_horizon_camera(az, alt, center_az, width, height, h_fov_deg, v_fov_deg):
    """Rectilinear camera with the horizon crossing the bottom-center pixel."""
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


def render_window_panel(l, b, g, bv, bortle, value, width, height, lat_deg, lst_hours,
                        center_az, az_width_deg, max_alt_deg, pct, gamma, normalization,
                        target_sky, white_pct, sky_pct, star_contrast, mode):
    az, alt = rh.gal_to_altaz(l, b, lat_deg, lst_hours)
    gain = gain_for_mag_delta(value) if mode != "snr" else float(value)
    L = rs.mag_to_luminance(g, 8.0)
    cols = rs.bv_to_rgb(bv)
    px, py, inside = project_horizon_camera(az, alt, center_az, width, height, az_width_deg, max_alt_deg)
    if mode == "snr":
        sky = rh.skyglow_level(bortle)
        L = sky_limited_snr(L, sky, gain)
        canvas = rs.accumulate_stars(height, width, px, py, inside, L, cols, psf_px=1.0)
    else:
        canvas = rs.accumulate_stars(height, width, px, py, inside, L * gain, cols, psf_px=1.0)
        canvas = add_skyglow(canvas, bortle)
    return normalize_panel(canvas, normalization, pct, gamma, target_sky, white_pct, sky_pct, star_contrast)


def render_perspective_panel(l, b, g, bv, bortle, value, width, height, lat_deg, lst_hours,
                             look_az, look_alt, fov_deg, pct, gamma, normalization,
                             target_sky, white_pct, sky_pct, star_contrast, mode):
    az, alt = rh.gal_to_altaz(l, b, lat_deg, lst_hours)
    gain = gain_for_mag_delta(value) if mode != "snr" else float(value)
    L = rs.mag_to_luminance(g, 8.0)
    cols = rs.bv_to_rgb(bv)
    px, py, inside = project_perspective_altaz(az, alt, look_az, look_alt, width, height, fov_deg)
    if mode == "snr":
        sky = rh.skyglow_level(bortle)
        L = sky_limited_snr(L, sky, gain)
        canvas = rs.accumulate_stars(height, width, px, py, inside, L, cols, psf_px=1.0)
    else:
        canvas = rs.accumulate_stars(height, width, px, py, inside, L * gain, cols, psf_px=1.0)
        canvas = add_skyglow(canvas, bortle)
    return normalize_panel(canvas, normalization, pct, gamma, target_sky, white_pct, sky_pct, star_contrast)


def render_equirect_panel(l, b, g, bv, bortle, value, width, height, lat_deg, lst_hours,
                          pct, gamma, normalization, target_sky, white_pct, sky_pct, star_contrast, mode):
    canvas, _az, _alt = rh.render_horizon_map(
        l,
        b,
        g,
        bv,
        lat_deg,
        lst_hours,
        width,
        height,
        m_ref=8.0,
        psf_px=1.0,
        gain=gain_for_mag_delta(value) if mode != "snr" else 1.0,
    )
    if mode == "snr":
        sky = rh.skyglow_level(bortle)
        # Approximate equirect SNR by applying sky-limited compression to rendered star signal.
        canvas = sky_limited_snr(canvas, sky, float(value))
    else:
        canvas = add_skyglow(canvas, bortle)
    return normalize_panel(canvas, normalization, pct, gamma, target_sky, white_pct, sky_pct, star_contrast)


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


def render_grid(data_path, output, bortles, values, panel_width, panel_height, lat_deg, lst_hours,
                pct, gamma, projection, look_az, look_alt, fov_deg, normalization, target_sky,
                white_pct, sky_pct, star_contrast, az_width_deg, max_alt_deg, mode, columns_per_row=None):
    from PIL import Image

    d = np.load(data_path)
    l, b, g = d["l"], d["b"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    panels_flat = []
    if look_az is None or look_alt is None:
        look_az, look_alt = galactic_center_altaz(lat_deg, lst_hours)
    for bortle in bortles:
        for value in values:
            if projection == "equirect":
                panel = render_equirect_panel(
                    l, b, g, bv, bortle, value, panel_width, panel_height,
                    lat_deg, lst_hours, pct, gamma, normalization, target_sky, white_pct, sky_pct, star_contrast, mode,
                )
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  equirect"
            elif projection == "horizon_window":
                panel = render_window_panel(
                    l, b, g, bv, bortle, value, panel_width, panel_height,
                    lat_deg, lst_hours, look_az, az_width_deg, max_alt_deg,
                    pct, gamma, normalization, target_sky, white_pct, sky_pct, star_contrast, mode,
                )
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  Beijing horizon"
            else:
                panel = render_perspective_panel(
                    l, b, g, bv, bortle, value, panel_width, panel_height, lat_deg, lst_hours,
                    look_az, look_alt, fov_deg, pct, gamma, normalization, target_sky, white_pct, sky_pct, star_contrast, mode,
                )
                label = f"Bortle {bortle}  {column_label(mode, value, bortle)}  Beijing wide"
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
    nelm = limiting_mag_for_sky(bortle, gain_for_mag_delta(value))
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
    p.add_argument("--max-alt-deg", type=float, default=75.0, help="Vertical FOV; bottom-center is horizon.")
    p.add_argument("--normalization", choices=["sky_median", "percentile"], default="sky_median")
    p.add_argument("--target-sky", type=float, default=0.03,
                   help="Linear sky RGB-channel level after adaptation normalization.")
    p.add_argument("--sky-pct", type=float, default=25.0,
                   help="Low percentile used as background sky estimate for adaptation.")
    p.add_argument("--star-contrast", type=float, default=4.0,
                   help="Contrast boost for signal above the adapted sky background.")
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
        args.az_width_deg,
        args.max_alt_deg,
        args.mode,
        args.columns_per_row,
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
