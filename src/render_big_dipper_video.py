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
)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=DATA_DEFAULT)
    p.add_argument("--frames-dir", default=os.path.join(OUTPUTS_DIR, "big_dipper_forward_frames"))
    p.add_argument("--output", default=os.path.join(OUTPUTS_DIR, "big_dipper_forward.mp4"))
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--distance-pc", type=float, default=400.0)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--fov-deg", type=float, default=170.0)
    p.add_argument("--look-dir", help="Override look direction as normalized x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--flight-dir", help="Override flight direction as normalized x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--pct", type=float, default=99.7)
    p.add_argument("--bloom-strength", type=float, default=0.5)
    p.add_argument("--bloom-sigma", type=float, default=5.0)
    p.add_argument("--save-hdr", action="store_true", help="Also keep 16-bit TIFF frames.")
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--no-mp4", action="store_true")
    return p


def config_from_args(args):
    default_dir = big_dipper_direction()
    return {
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "distance_pc": args.distance_pc,
        "look_dir": parse_triplet(args.look_dir) if args.look_dir else default_dir,
        "flight_dir": parse_triplet(args.flight_dir) if args.flight_dir else default_dir,
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
