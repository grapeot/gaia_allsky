"""Locate the Great Rift 'rift core' and 'bright cloud' probing windows from
G<13 Gaia data, and compute which Flatiron HEALPix-8 shards cover each window.

Reusable pieces for downstream probing:
  - window_star_count(l,b,g, lc,bc,radius_deg) : measured G<13 count in a disk
  - window_to_healpix8(lc,bc,radius_deg)       : set of HEALPix-8 nested indices
  - shards_for_indices(idx, manifest)          : manifest rows whose
                                                 [healpix8_min,healpix8_max] overlap
Read-only: never opens the .gz shards, only manifest.csv.
"""
import os
import csv
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "raw", "gaia_g13.npz")
SHARD_DIR = os.path.join(ROOT, "data", "raw", "flatiron_gaia_source_fov_gz")
MANIFEST = os.path.join(SHARD_DIR, "manifest.csv")

# Render camera (from src/render_bortle_eye_grid.py defaults)
LAT_DEG = 23.13
LST_HOURS = 17.76
H_FOV = 90.0          # az_width_deg -> horizontal FOV for horizon_window camera
PANEL_W, PANEL_H = 1080, 1920

import sys
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_horizon as rh
from render_bortle_eye_grid import project_horizon_camera, galactic_center_altaz


# ---------------------------------------------------------------------------
# angular distance on the sphere (great-circle), all in galactic deg
# ---------------------------------------------------------------------------
def angsep_deg(l1, b1, l2, b2):
    l1, b1, l2, b2 = map(np.radians, (l1, b1, l2, b2))
    sin = (np.sin(b1) * np.sin(b2)
           + np.cos(b1) * np.cos(b2) * np.cos(l1 - l2))
    return np.degrees(np.arccos(np.clip(sin, -1, 1)))


def window_star_count(l, b, g, lc, bc, radius_deg, gmax=13.0):
    """Measured G<gmax star count within radius_deg of (lc,bc)."""
    m = g < gmax
    sep = angsep_deg(l[m], b[m], lc, bc)
    return int(np.count_nonzero(sep <= radius_deg))


# ---------------------------------------------------------------------------
# Task 1: build a 1deg x 1deg count map in the camera FOV near the GC
# ---------------------------------------------------------------------------
def build_density_map(l, b, g, gmax=13.0):
    look_az, _ = galactic_center_altaz(LAT_DEG, LST_HOURS)
    # candidate region near galactic center
    region = (g < gmax) & (np.abs(b) < 10.0) & (
        ((l >= 330) | (l <= 30)))
    lr, br = l[region], b[region]
    # project to camera, keep only stars inside the rendered panel
    az, alt = rh.gal_to_altaz(lr, br, LAT_DEG, LST_HOURS)
    _, _, inside = project_horizon_camera(
        az, alt, look_az, PANEL_W, PANEL_H, H_FOV, 75.0, "horizontal")
    lr, br = lr[inside], br[inside]
    # wrap l to [-30,30] for a contiguous grid
    lw = ((lr + 180) % 360) - 180
    # 1deg grid over l in [-30,30], b in [-10,10]
    lbins = np.arange(-30, 31, 1.0)
    bbins = np.arange(-10, 11, 1.0)
    H, _, _ = np.histogram2d(lw, br, bins=[lbins, bbins])
    lcent = 0.5 * (lbins[:-1] + lbins[1:])
    bcent = 0.5 * (bbins[:-1] + bbins[1:])
    return H, lcent, bcent, look_az


def find_windows(l, b, g):
    H, lcent, bcent, look_az = build_density_map(l, b, g)
    # cloud = highest-density cell in the FOV
    ci = np.unravel_index(np.argmax(H), H.shape)
    cloud_l = lcent[ci[0]] % 360.0
    cloud_b = bcent[ci[1]]
    # rift = darkest cell near the plane (|b|<5) that lies inside the dust band.
    # Find local minimum of the count map within |b|<5, excluding the far
    # wings (require l within +-25 so it's genuinely the central rift).
    bmask = np.abs(bcent) < 5.0
    lmask = np.abs(lcent) <= 25.0
    sub = H.copy()
    big = sub.max() + 1
    sub[~lmask, :] = big
    sub[:, ~bmask] = big
    ri = np.unravel_index(np.argmin(sub), sub.shape)
    rift_l = lcent[ri[0]] % 360.0
    rift_b = bcent[ri[1]]
    return (rift_l, rift_b), (cloud_l, cloud_b), H, lcent, bcent


# ---------------------------------------------------------------------------
# Task 2: window -> HEALPix-8 nested indices -> Flatiron shards
# ---------------------------------------------------------------------------
def window_to_healpix8(lc, bc, radius_deg, n_ring=8, n_rad=4):
    """Sample a disk (center+rings) in galactic coords, convert to ICRS,
    return the set of nested HEALPix level-8 (nside=256) indices covered."""
    from astropy_healpix import HEALPix
    from astropy.coordinates import SkyCoord, Galactic, ICRS
    import astropy.units as u

    hp = HEALPix(nside=256, order="nested", frame=ICRS())
    ls = [lc]
    bs = [bc]
    # concentric rings out to radius_deg; use proper offset on the sphere
    for rr in np.linspace(radius_deg / n_rad, radius_deg, n_rad):
        for th in np.linspace(0, 2 * np.pi, n_ring * 4, endpoint=False):
            # offset by angle rr along bearing th from (lc,bc)
            b1 = np.radians(bc)
            d = np.radians(rr)
            b2 = np.arcsin(np.sin(b1) * np.cos(d)
                           + np.cos(b1) * np.sin(d) * np.cos(th))
            dl = np.arctan2(np.sin(th) * np.sin(d) * np.cos(b1),
                            np.cos(d) - np.sin(b1) * np.sin(b2))
            ls.append((lc + np.degrees(dl)) % 360.0)
            bs.append(np.degrees(b2))
    gal = SkyCoord(l=np.array(ls) * u.deg, b=np.array(bs) * u.deg,
                   frame=Galactic)
    icrs = gal.icrs
    idx = hp.lonlat_to_healpix(icrs.ra, icrs.dec)
    return set(int(i) for i in np.unique(idx))


def load_manifest():
    rows = []
    with open(MANIFEST) as f:
        for r in csv.DictReader(f):
            rows.append({
                "name": r["name"],
                "lo": int(r["healpix8_min"]),
                "hi": int(r["healpix8_max"]),
                "size": int(r["size_bytes"]),
            })
    return rows


def shards_for_indices(indices, manifest):
    idx = np.array(sorted(indices))
    hits = []
    for row in manifest:
        # shard covers contiguous [lo,hi]; overlap if any index falls inside
        if np.any((idx >= row["lo"]) & (idx <= row["hi"])):
            hits.append(row)
    return hits


def report_shards(label, lc, bc, radius_deg, manifest):
    idx = window_to_healpix8(lc, bc, radius_deg)
    shards = shards_for_indices(idx, manifest)
    print(f"\n[{label}] window center (l,b)=({lc:.2f},{bc:.2f}) r={radius_deg} deg")
    print(f"  HEALPix-8 indices touched: {len(idx)} "
          f"(range {min(idx)}..{max(idx)})")
    print(f"  shards needed: {len(shards)}")
    total = 0
    missing = []
    for s in shards:
        path = os.path.join(SHARD_DIR, s["name"])
        exists = os.path.exists(path)
        total += s["size"]
        if not exists:
            missing.append(s["name"])
        print(f"    {s['name']:40s} hp8 {s['lo']}..{s['hi']:>6} "
              f"{s['size']/1e6:8.1f} MB  {'OK' if exists else 'MISSING'}")
    print(f"  total size: {total/1e9:.2f} GB ({total/1e6:.1f} MB)")
    if missing:
        print(f"  MISSING {len(missing)}: {missing}")
    else:
        print("  all shards present locally")
    return shards, total, missing


def main():
    d = np.load(DATA)
    l, b, g = d["l"], d["b"], d["g"]
    print(f"loaded {l.size} stars; G<13: {np.count_nonzero(g<13)}")

    (rift_l, rift_b), (cloud_l, cloud_b), H, lc, bc = find_windows(l, b, g)
    R = 2.0
    rift_n = window_star_count(l, b, g, rift_l, rift_b, R)
    cloud_n = window_star_count(l, b, g, cloud_l, cloud_b, R)

    print("\n=== TASK 1: WINDOWS ===")
    print(f"RIFT  center (l,b)=({rift_l:.2f},{rift_b:.2f}) r={R} deg  "
          f"G<13 count={rift_n}")
    print(f"CLOUD center (l,b)=({cloud_l:.2f},{cloud_b:.2f}) r={R} deg  "
          f"G<13 count={cloud_n}")
    print(f"ratio cloud/rift = {cloud_n/max(rift_n,1):.2f}x")

    print("\n=== TASK 2: SHARDS ===")
    manifest = load_manifest()
    report_shards("RIFT", rift_l, rift_b, R, manifest)
    report_shards("CLOUD", cloud_l, cloud_b, R, manifest)


if __name__ == "__main__":
    main()
