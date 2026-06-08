"""Render a pure equirectangular VR flight video."""
import argparse
import os

from video_common import (
    DATA_DEFAULT,
    OUTPUTS_DIR,
    assemble_mp4,
    big_dipper_direction,
    galactic_pole_direction,
    render_frames_parallel,
    render_vr_frame,
    resolve_frame_count,
    shared_l_positions,
)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=DATA_DEFAULT)
    p.add_argument("--frames-dir", default=os.path.join(OUTPUTS_DIR, "vr_equirect_frames"))
    p.add_argument("--output", default=os.path.join(OUTPUTS_DIR, "vr_equirect.mp4"))
    p.add_argument("--width", type=int, default=2048)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--duration", type=float, help="Video duration in seconds. Overrides --frames when set.")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--leg1-pc", type=float, default=400.0)
    p.add_argument("--leg2-pc", type=float, default=2500.0)
    p.add_argument("--split", type=float, default=0.5)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--pct", type=float, default=99.7)
    p.add_argument("--bloom-strength", type=float, default=0.5)
    p.add_argument("--bloom-sigma", type=float, default=6.0)
    p.add_argument("--save-hdr", action="store_true", help="Also keep 16-bit TIFF frames.")
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--no-mp4", action="store_true")
    return p


def config_from_args(args):
    frames = resolve_frame_count(args.frames, args.fps, args.duration)
    positions, _phase = shared_l_positions(
        frames,
        args.leg1_pc,
        args.leg2_pc,
        args.split,
        leg1_dir=big_dipper_direction(),
        leg2_dir=galactic_pole_direction(),
    )
    return {
        "width": args.width,
        "height": args.height,
        "frames": frames,
        "positions": positions,
        "gamma": args.gamma,
        "pct": args.pct,
        "bloom_strength": args.bloom_strength,
        "bloom_sigma": args.bloom_sigma,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)
    render_frames_parallel(args.data, args.frames_dir, cfg, render_vr_frame, args.workers, args.save_hdr)
    if not args.no_mp4:
        assemble_mp4(args.frames_dir, args.output, args.fps, args.crf)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
