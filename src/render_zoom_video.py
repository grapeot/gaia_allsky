"""全景→银心核 1:1 的 zoom-in 视频。

每一帧是一个固定切点、fov 指数缩小的 TAN 投影（复用 render_tan_wcs.render_tile）。
fov 从起点（全景，看到大半银河带）指数缩到终点（1:1，每像素≈最高分辨率原生像
素）。等比缩小 → 视觉上匀速 zoom。每帧独立 → 多进程并行。立体角归一化保证各
帧（不同 fov）亮度一致，不会 zoom 时忽明忽暗。

帧渲完用 ffmpeg 合成 mp4（H.264，便于网页/分享）。

用法：
  python src/render_zoom_video.py --data data/raw/fov_g20.npz \
      --out outputs/zoom_milkyway.mp4 --lc 0 --bc -2 \
      --fov-start 70 --fov-end 1.9 --size 640 --seconds 10 --fps 30 --workers 8
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import render_starmap as rs
import render_bortle_eye_grid as beg
from render_tan_wcs import render_tile

_SHARED = None


def _frame_worker(job):
    """渲一帧（固定切点、给定 fov）到 PNG。复用 render_tile 但只要 PNG（丢 .hhh）。"""
    idx, fov, png = job
    s = _SHARED
    prefix = png[:-4]  # render_tile 写 prefix.png + prefix.hhh
    render_tile(s["l"], s["b"], s["cols"], s["L"], prefix,
                s["lc"], s["bc"], fov, s["size"], **s["tile_kw"])
    hhh = prefix + ".hhh"
    if os.path.exists(hhh):
        os.remove(hhh)
    return idx


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lc", type=float, default=0.0, help="终点切点银经")
    ap.add_argument("--bc", type=float, default=-2.0, help="终点切点银纬")
    ap.add_argument("--fov-start", type=float, default=70.0)
    ap.add_argument("--fov-end", type=float, default=1.9, help="1:1：640/341≈1.9°")
    ap.add_argument("--size", type=int, default=640)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--frames-dir", default=None,
                    help="帧输出目录（指定则保留帧不删，便于后续续接/重剪）")
    ap.add_argument("--keep-frames", action="store_true", help="渲完保留帧")
    # tone（默认比静态图调亮一档：target_white 2.5→2.0，star_contrast 6→7）
    ap.add_argument("--psf-core-px", type=float, default=0.6)
    ap.add_argument("--target-sky", type=float, default=0.012)
    ap.add_argument("--star-contrast", type=float, default=7.0)
    ap.add_argument("--chroma", type=float, default=1.8)
    ap.add_argument("--target-white", type=float, default=2.0)
    ap.add_argument("--bortle", type=int, default=1)
    args = ap.parse_args()

    n_frames = int(round(args.seconds * args.fps))
    # 指数（等比）fov 序列：fov[i] = start * (end/start)^(i/(N-1))
    ratio = args.fov_end / args.fov_start
    fovs = args.fov_start * ratio ** (np.arange(n_frames) / max(n_frames - 1, 1))
    print(f"{n_frames} 帧, fov {args.fov_start}°→{args.fov_end}° (等比), "
          f"{args.size}px, 终点切点({args.lc},{args.bc}), {args.workers} 进程", flush=True)

    with np.load(args.data) as d:
        l, b, g = d["l"][:], d["b"][:], d["g"][:]
        bv = np.nan_to_num(d["bp_rp"][:], nan=0.7)
    cols = rs.bv_to_rgb(bv)
    L = beg.visual_luminance_for_mags(g, args.bortle, 0.0, 0.5)

    if args.frames_dir:
        frames_dir = args.frames_dir
        os.makedirs(frames_dir, exist_ok=True)
    else:
        frames_dir = tempfile.mkdtemp(prefix="zoom_frames_", dir=os.path.join(ROOT, "outputs"))
    tile_kw = dict(psf_core_px=args.psf_core_px, bortle=args.bortle,
                   target_sky=args.target_sky, star_contrast=args.star_contrast,
                   chroma=args.chroma, target_white=args.target_white)
    jobs = [(i, float(fovs[i]), os.path.join(frames_dir, f"f{i:04d}.png"))
            for i in range(n_frames)]

    global _SHARED
    _SHARED = dict(l=l, b=b, cols=cols, L=L, lc=args.lc, bc=args.bc,
                   size=args.size, tile_kw=tile_kw)
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    done = 0
    # 每帧 worker 自己即时 Image.save 到最终路径（崩了也只丢未渲的，已渲的留盘）。
    # 进度逐帧打印（带 idx），方便实时看到进度、定位卡在哪一帧。
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
        for fut in as_completed([ex.submit(_frame_worker, j) for j in jobs]):
            idx = fut.result(); done += 1
            print(f"  帧 {done}/{n_frames}（f{idx:04d}）", flush=True)

    # ffmpeg 合成 H.264 mp4
    print("ffmpeg 合成 ...", flush=True)
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(args.fps),
        "-i", os.path.join(frames_dir, "f%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        args.out,
    ], check=True, capture_output=True)
    print(f"wrote {args.out}", flush=True)
    # 默认临时帧目录用完即删；--frames-dir 或 --keep-frames 则保留
    if args.frames_dir or args.keep_frames:
        print(f"帧保留在 {frames_dir}/", flush=True)
    else:
        for j in jobs:
            if os.path.exists(j[2]):
                os.remove(j[2])
        os.rmdir(frames_dir)


if __name__ == "__main__":
    main()
