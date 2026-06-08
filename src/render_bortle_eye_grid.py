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


def add_skyglow(canvas, bortle):
    return canvas + rh.skyglow_level(bortle)


def normalize_sky_adapted(canvas, target_sky=0.12, gamma=2.2):
    """Normalize like eye/camera adaptation: median sky maps to a stable gray level."""
    y = canvas.sum(axis=-1)
    median_sky = float(np.median(y))
    scale = target_sky / max(median_sky, 1e-9)
    return np.clip(canvas * scale, 0, 1) ** (1 / gamma)


def normalize_panel(canvas, mode, pct, gamma, target_sky):
    if mode == "sky_median":
        return (normalize_sky_adapted(canvas, target_sky, gamma) * 255).astype(np.uint8)
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


def render_perspective_panel(l, b, g, bv, bortle, nelm, width, height, lat_deg, lst_hours,
                             look_az, look_alt, fov_deg, pct, gamma, normalization, target_sky):
    az, alt = rh.gal_to_altaz(l, b, lat_deg, lst_hours)
    L = rs.mag_to_luminance(g, 8.0) * gain_for_nelm(nelm)
    cols = rs.bv_to_rgb(bv)
    px, py, inside = project_perspective_altaz(az, alt, look_az, look_alt, width, height, fov_deg)
    canvas = rs.accumulate_stars(height, width, px, py, inside, L, cols, psf_px=1.0)
    canvas = add_skyglow(canvas, bortle)
    return normalize_panel(canvas, normalization, pct, gamma, target_sky)


def render_equirect_panel(l, b, g, bv, bortle, nelm, width, height, lat_deg, lst_hours,
                          pct, gamma, normalization, target_sky):
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
        gain=gain_for_nelm(nelm),
    )
    canvas = add_skyglow(canvas, bortle)
    return normalize_panel(canvas, normalization, pct, gamma, target_sky)


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


def render_grid(data_path, output, bortles, nelms, panel_width, panel_height, lat_deg, lst_hours,
                pct, gamma, projection, look_az, look_alt, fov_deg, normalization, target_sky):
    from PIL import Image

    d = np.load(data_path)
    l, b, g = d["l"], d["b"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    rows = []
    if look_az is None or look_alt is None:
        look_az, look_alt = galactic_center_altaz(lat_deg, lst_hours)
    for bortle in bortles:
        panels = []
        for nelm in nelms:
            if projection == "equirect":
                panel = render_equirect_panel(
                    l, b, g, bv, bortle, nelm, panel_width, panel_height,
                    lat_deg, lst_hours, pct, gamma, normalization, target_sky,
                )
                label = f"Bortle {bortle}  NELM {nelm:g}  equirect"
            else:
                panel = render_perspective_panel(
                    l, b, g, bv, bortle, nelm, panel_width, panel_height, lat_deg, lst_hours,
                    look_az, look_alt, fov_deg, pct, gamma, normalization, target_sky,
                )
                label = f"Bortle {bortle}  NELM {nelm:g}  Beijing wide"
            panels.append(label_panel(panel, label))
        rows.append(np.concatenate(panels, axis=1))
    grid = np.concatenate(rows, axis=0)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    Image.fromarray(grid).save(output)
    return output


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=DATA_DEFAULT)
    p.add_argument("--output", default=OUTPUT_DEFAULT)
    p.add_argument("--bortles", default="1,6")
    p.add_argument("--nelms", default="6,8,11")
    p.add_argument("--panel-width", type=int, default=960)
    p.add_argument("--panel-height", type=int, default=540)
    p.add_argument("--lat-deg", type=float, default=39.9)
    p.add_argument("--lst-hours", type=float, default=17.76, help="LST near galactic-center culmination in Beijing.")
    p.add_argument("--projection", choices=["perspective", "equirect"], default="perspective")
    p.add_argument("--look-az", type=float)
    p.add_argument("--look-alt", type=float)
    p.add_argument("--fov-deg", type=float, default=110.0)
    p.add_argument("--normalization", choices=["sky_median", "percentile"], default="sky_median")
    p.add_argument("--target-sky", type=float, default=0.12,
                   help="Median sky RGB-channel level after adaptation normalization.")
    p.add_argument("--pct", type=float, default=99.7)
    p.add_argument("--gamma", type=float, default=2.2)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    out = render_grid(
        args.data,
        args.output,
        parse_csv_numbers(args.bortles, int),
        parse_csv_numbers(args.nelms, float),
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
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
