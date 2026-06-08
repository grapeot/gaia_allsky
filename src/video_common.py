"""Shared helpers for Gaia video rendering CLIs."""
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

import render_3d as r3
import render_starmap as rs


DATA_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep.npz")
OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs")


_CTX = None


def ease(t):
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(t, 0, 1))


def unit_from_radec(ra_deg, dec_deg):
    ra = np.radians(ra_deg)
    dec = np.radians(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def big_dipper_direction():
    """Approximate direction toward the Big Dipper asterism center."""
    stars = np.array([
        [165.93, 61.75],
        [165.46, 56.38],
        [178.46, 53.69],
        [183.86, 57.03],
        [193.51, 55.96],
        [200.98, 54.93],
        [206.89, 49.31],
    ])
    vec = np.array([unit_from_radec(ra, dec) for ra, dec in stars]).mean(axis=0)
    return vec / np.linalg.norm(vec)


def parse_triplet(text):
    parts = [float(x.strip()) for x in text.split(",")]
    if len(parts) != 3:
        raise ValueError("direction must be three comma-separated floats")
    v = np.array(parts, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("direction vector must be non-zero")
    return v / n


def expose(canvas, gamma, pct):
    return rs.normalize_brightness(canvas, pct, "gamma", gamma)


def init_worker(data_path, config):
    global _CTX
    d = np.load(data_path)
    ra, dec, dist_pc, g = d["ra"], d["dec"], d["dist_pc"], d["g"]
    bv = np.nan_to_num(d["bp_rp"], nan=0.7)
    _CTX = {
        "xyz": r3._radec_dist_to_xyz(ra, dec, dist_pc),
        "g": g,
        "bv": bv,
        "config": config,
    }


def render_vr_frame(i):
    cfg = _CTX["config"]
    t = i / max(cfg["frames"] - 1, 1)
    obs = cfg["flight_dir"] * (ease(t) * cfg["distance_pc"])
    canvas = r3.render_3d_frame(
        _CTX["xyz"], _CTX["g"], _CTX["bv"], obs, cfg["width"], cfg["height"],
        gain=1.0, bloom=True, bloom_strength=cfg["bloom_strength"], bloom_sigma=cfg["bloom_sigma"],
    )
    return expose(canvas, cfg["gamma"], cfg["pct"])


def render_forward_frame(i):
    cfg = _CTX["config"]
    t = i / max(cfg["frames"] - 1, 1)
    obs = cfg["flight_dir"] * (ease(t) * cfg["distance_pc"])
    side = min(cfg["width"], cfg["height"])
    disk = r3.render_fisheye_lookdir(
        _CTX["xyz"], _CTX["g"], _CTX["bv"], obs, cfg["look_dir"], side,
        fov_deg=cfg["fov_deg"], gain=1.0, bloom=True,
        bloom_strength=cfg["bloom_strength"], bloom_sigma=cfg["bloom_sigma"],
    )
    lin = expose(disk, cfg["gamma"], cfg["pct"])
    frame = np.zeros((cfg["height"], cfg["width"], 3), np.float32)
    y0 = (cfg["height"] - side) // 2
    x0 = (cfg["width"] - side) // 2
    frame[y0:y0 + side, x0:x0 + side] = lin
    return frame


def write_frame(index, frame, outdir, save_hdr):
    from PIL import Image

    Image.fromarray((np.clip(frame, 0, 1) * 255).astype("uint8")).save(
        os.path.join(outdir, f"frame_{index:04d}.png")
    )
    if save_hdr:
        import tifffile

        tifffile.imwrite(
            os.path.join(outdir, f"frame_{index:04d}.tif"),
            (np.clip(frame, 0, 1) * 65535).astype("uint16"),
        )


def render_and_write_frame(index, frame_func, outdir, save_hdr):
    frame = frame_func(index)
    write_frame(index, frame, outdir, save_hdr)
    return index


def render_frames_parallel(data_path, outdir, config, frame_func, workers=None, save_hdr=False):
    workers = workers or (os.cpu_count() or 1)
    os.makedirs(outdir, exist_ok=True)
    print(f"rendering {config['frames']} frames to {outdir} with {workers} workers")
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(data_path, config)) as ex:
        futures = {
            ex.submit(render_and_write_frame, i, frame_func, outdir, save_hdr): i
            for i in range(config["frames"])
        }
        done = 0
        for fut in as_completed(futures):
            fut.result()
            done += 1
            if done == 1 or done % max(1, config["frames"] // 10) == 0 or done == config["frames"]:
                print(f"  {done}/{config['frames']} frames")
    return outdir


def assemble_mp4(frames_dir, out_path, fps=60, crf=16):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path
