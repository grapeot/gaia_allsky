"""Shared helpers for Gaia video rendering CLIs."""
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

import render_3d as r3
import render_starmap as rs
import motion


DATA_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_3d_deep.npz")
OUTPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs")


_CTX = None


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


def big_dipper_xyz():
    """Approximate 3D positions for the seven Big Dipper stars in parsec."""
    stars = np.array([
        [165.93, 61.75, 123.0],
        [165.46, 56.38, 79.0],
        [178.46, 53.69, 84.0],
        [183.86, 57.03, 81.0],
        [193.51, 55.96, 81.0],
        [200.98, 54.93, 83.0],
        [206.89, 49.31, 104.0],
    ])
    return r3._radec_dist_to_xyz(stars[:, 0], stars[:, 1], stars[:, 2])


def galactic_center_direction():
    return r3.flight_direction("galactic_plane")


def galactic_pole_direction():
    return r3.flight_direction("galactic_pole")


def parse_triplet(text):
    parts = [float(x.strip()) for x in text.split(",")]
    if len(parts) != 3:
        raise ValueError("direction must be three comma-separated floats")
    v = np.array(parts, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("direction vector must be non-zero")
    return v / n


def resolve_frame_count(frames, fps, duration):
    if duration is None:
        if frames <= 0:
            raise ValueError("frames must be positive")
        return frames
    if duration <= 0:
        raise ValueError("duration must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")
    return max(1, int(round(duration * fps)))


def expose(canvas, gamma, pct):
    return rs.normalize_brightness(canvas, pct, "gamma", gamma)


def add_psf_cli_args(p):
    """给视频 CLI 注册统一 PSF + 饱和溢出 + 暗星截断补偿参数(与静态图同名同义)。

    饱和锚点用参考星等而非 skyglow: 飞行视角没有 skyglow/Bortle/NELM, 改成"视星等
    亮于 sat-ref-mag 的恒星触发饱和溢出"。这个阈值是纯物理量, 整段视频恒定, 不随
    观测者移动逐帧抖动。
    """
    p.add_argument("--psf-core-px", type=float, default=1.1,
                   help="Shared Gaussian PSF sigma in pixels applied to every star.")
    p.add_argument("--faint-gain", type=float, default=4.2,
                   help="Luminance gain for catalog G >= faint-mag-min stars, standing in for "
                        "the integrated light lost to the G=11 3D-catalog truncation.")
    p.add_argument("--faint-mag-min", type=float, default=9.0,
                   help="Catalog-G threshold above which the truncation gain applies. The mask "
                        "uses intrinsic catalog G (not reprojected vis_mag), so star identity is "
                        "stable as the observer moves.")
    p.add_argument("--sat-over-ref", type=float, default=6.0,
                   help="Saturation level as a multiple of the luminance of a --sat-ref-mag star. "
                        "Energy above it is redistributed into wide scattering wings. <=0 disables.")
    p.add_argument("--sat-ref-mag", type=float, default=r3.SAT_REF_MAG_DEFAULT,
                   help="Reference visual magnitude anchoring the saturation line; held fixed "
                        "for the whole video so saturation onset does not jitter frame to frame.")
    p.add_argument("--wing-sigmas", default="3,9",
                   help="Gaussian sigmas (px, CSV) for the saturation scattering wings.")
    p.add_argument("--wing-weights", default="0.65,0.35",
                   help="Energy weights (CSV) for the saturation scattering wings.")
    return p


def _parse_csv_floats(text):
    return tuple(float(x.strip()) for x in text.split(",") if x.strip())


def psf_config_from_args(args):
    """把统一 PSF CLI 参数折叠成 config 字典字段(含预算好的固定 sat_level)。"""
    return {
        "psf_core_px": args.psf_core_px,
        "faint_gain": args.faint_gain,
        "faint_mag_min": args.faint_mag_min,
        "sat_level": r3.sat_level_from_ref_mag(args.sat_over_ref, args.sat_ref_mag),
        "wing_sigmas": _parse_csv_floats(args.wing_sigmas),
        "wing_weights": _parse_csv_floats(args.wing_weights),
    }


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


def _psf_kwargs(cfg):
    """统一 PSF 成像参数, 从 config 取出传给 render_3d 各渲染函数。"""
    return dict(
        gain=1.0,
        psf_core_px=cfg["psf_core_px"],
        faint_gain=cfg["faint_gain"],
        faint_mag_min=cfg["faint_mag_min"],
        sat_level=cfg["sat_level"],
        wing_sigmas=cfg["wing_sigmas"],
        wing_weights=cfg["wing_weights"],
    )


def render_vr_frame(i):
    cfg = _CTX["config"]
    obs = cfg["positions"][i]
    canvas = r3.render_3d_frame(
        _CTX["xyz"], _CTX["g"], _CTX["bv"], obs, cfg["width"], cfg["height"],
        **_psf_kwargs(cfg),
    )
    return expose(canvas, cfg["gamma"], cfg["pct"])


def render_forward_frame(i):
    cfg = _CTX["config"]
    obs = cfg["positions"][i]
    look_dir = cfg["look_dirs"][i]
    if cfg["projection"] == "fisheye":
        side = min(cfg["width"], cfg["height"])
        disk = r3.render_fisheye_lookdir(
            _CTX["xyz"], _CTX["g"], _CTX["bv"], obs, look_dir, side,
            fov_deg=cfg["fov_deg"], **_psf_kwargs(cfg),
        )
        lin = expose(disk, cfg["gamma"], cfg["pct"])
        frame = np.zeros((cfg["height"], cfg["width"], 3), np.float32)
        y0 = (cfg["height"] - side) // 2
        x0 = (cfg["width"] - side) // 2
        frame[y0:y0 + side, x0:x0 + side] = lin
        return frame
    canvas = r3.render_perspective_lookdir(
        _CTX["xyz"], _CTX["g"], _CTX["bv"], obs, look_dir, cfg["width"], cfg["height"],
        fov_deg=cfg["fov_deg"], **_psf_kwargs(cfg),
    )
    frame = expose(canvas, cfg["gamma"], cfg["pct"])
    if cfg.get("dipper_overlay", False):
        frame = draw_dipper_overlay(frame, obs, look_dir, cfg["fov_deg"], cfg.get("overlay_width", 0))
    return frame


def project_perspective_points(xyz_points, obs_pos, look_dir, W, H, fov_deg):
    rel = xyz_points - obs_pos[None, :]
    d = np.sqrt((rel ** 2).sum(-1))
    svec = rel / np.maximum(d[:, None], 1e-9)
    forward = look_dir / np.linalg.norm(look_dir)
    up_hint = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(forward, up_hint)) > 0.95:
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
    px = (nx * 0.5 + 0.5) * W
    py = (0.5 - ny * 0.5) * H
    inside = (z > 0) & (np.abs(nx) <= 1) & (np.abs(ny) <= 1)
    return np.stack([px, py], axis=-1), inside


def overlay_width_for_frame(width_px, height_px, requested=0):
    """北斗连线线宽。requested>0 时用显式值；否则按画幅自适应。

    线宽是绝对像素，必须随分辨率缩放：1px 线在 2160 画幅渲染、再缩到 720
    预览后只剩 1/3 像素，经 H.264 压缩后完全不可见（2026-06-09 实际踩坑）。
    基准是 720px 画幅约 1px。
    """
    if requested and requested > 0:
        return int(requested)
    return max(1, round(min(width_px, height_px) / 720))


def draw_dipper_overlay(frame, obs_pos, look_dir, fov_deg, width=0):
    from PIL import Image, ImageDraw

    H, W = frame.shape[:2]
    width = overlay_width_for_frame(W, H, width)
    pts, inside = project_perspective_points(big_dipper_xyz(), obs_pos, look_dir, W, H, fov_deg)
    img = Image.fromarray((np.clip(frame, 0, 1) * 255).astype("uint8"))
    draw = ImageDraw.Draw(img, "RGBA")
    color = (160, 190, 255, 150)
    dot_color = (210, 225, 255, 190)
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]:
        if inside[a] and inside[b]:
            draw.line((pts[a, 0], pts[a, 1], pts[b, 0], pts[b, 1]), fill=color, width=width)
    for p, ok in zip(pts, inside):
        if ok:
            r = max(2, width + 1)
            draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), outline=dot_color, width=width)
    return np.asarray(img).astype(np.float32) / 255.0


def shared_l_positions(frames, leg1_pc, leg2_pc, split, leg1_dir=None, leg2_dir=None, leg2_target=None):
    return motion.l_motion(
        frames,
        leg1_pc=leg1_pc,
        leg2_pc=leg2_pc,
        split=split,
        leg1_dir=leg1_dir,
        leg2_dir=leg2_dir,
        leg2_target=leg2_target,
    )


def shared_l_look_dirs(frames, start_dir, end_dir, phase):
    return motion.look_path(frames, start_dir, end_dir, phase)


def shared_l_look_at_dirs(positions, start_dir, target_point, phase):
    """Look from start_dir toward a fixed target point, using phase for smooth transition."""
    target_dirs = np.array([motion.normalize(target_point - pos) for pos in positions])
    return np.array([motion.slerp(start_dir, target_dirs[i], phase[i]) for i in range(len(positions))])


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
    for old in list(Path(outdir).glob("frame_*.png")) + list(Path(outdir).glob("frame_*.tif")):
        old.unlink()
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


def assemble_mp4(frames_dir, out_path, fps=60, crf=18, codec="libx265"):
    """帧序列合成 mp4。默认 H.265（libx265 + hvc1 tag，Safari/Chrome 均可播）。

    H.265 同质量下码率约为 H.264 的一半。hvc1 tag 是 Safari 识别 HEVC 的
    必要条件；不加 tag Safari 黑屏。codec="libx264" 可回退旧编码。
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "frame_%04d.png"),
        "-c:v", codec, "-pix_fmt", "yuv420p", "-crf", str(crf),
    ]
    if codec == "libx265":
        cmd += ["-tag:v", "hvc1"]
    cmd += ["-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)
    return out_path
