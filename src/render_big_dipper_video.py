"""Render a forward fisheye flight looking toward the Big Dipper."""
import argparse
import os

from video_common import (
    DATA_DEFAULT,
    OUTPUTS_DIR,
    assemble_mp4,
    big_dipper_direction,
    parse_triplet,
    render_forward_frame,
    render_frames_parallel,
    shared_l_look_dirs,
    shared_l_positions,
)
import render_3d as r3


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=DATA_DEFAULT)
    p.add_argument("--frames-dir", default=os.path.join(OUTPUTS_DIR, "big_dipper_forward_frames"))
    p.add_argument("--output", default=os.path.join(OUTPUTS_DIR, "big_dipper_forward.mp4"))
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--leg1-pc", type=float, default=400.0)
    p.add_argument("--leg2-pc", type=float, default=2500.0)
    p.add_argument("--split", type=float, default=0.5)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--projection", choices=["perspective", "fisheye"], default="perspective")
    p.add_argument("--fov-deg", type=float, default=100.0)
    p.add_argument("--start-look-dir", help="Override initial look direction as x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--end-look-dir", help="Override final look direction as x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--pct", type=float, default=99.7)
    p.add_argument("--bloom-strength", type=float, default=0.5)
    p.add_argument("--bloom-sigma", type=float, default=5.0)
    p.add_argument("--save-hdr", action="store_true", help="Also keep 16-bit TIFF frames.")
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--no-mp4", action="store_true")
    return p


def config_from_args(args):
    positions, phase = shared_l_positions(args.frames, args.leg1_pc, args.leg2_pc, args.split)
    start_dir = parse_triplet(args.start_look_dir) if args.start_look_dir else big_dipper_direction()
    end_dir = parse_triplet(args.end_look_dir) if args.end_look_dir else r3.flight_direction("galactic_pole")
    return {
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "positions": positions,
        "look_dirs": shared_l_look_dirs(args.frames, start_dir, end_dir, phase),
        "projection": args.projection,
        "fov_deg": args.fov_deg,
        "gamma": args.gamma,
        "pct": args.pct,
        "bloom_strength": args.bloom_strength,
        "bloom_sigma": args.bloom_sigma,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    render_frames_parallel(args.data, args.frames_dir, config_from_args(args), render_forward_frame, args.workers, args.save_hdr)
    if not args.no_mp4:
        assemble_mp4(args.frames_dir, args.output, args.fps, args.crf)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
