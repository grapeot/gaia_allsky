"""Render a forward flight looking toward the Big Dipper, then the galactic disk."""
import argparse
import os

import numpy as np

from video_common import (
    DATA_DEFAULT,
    OUTPUTS_DIR,
    add_psf_cli_args,
    assemble_mp4,
    big_dipper_direction,
    galactic_center_direction,
    galactic_pole_direction,
    parse_triplet,
    psf_config_from_args,
    render_forward_frame,
    render_frames_parallel,
    resolve_frame_count,
    shared_l_look_at_dirs,
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
    p.add_argument("--duration", type=float, help="Video duration in seconds. Overrides --frames when set.")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--leg1-pc", type=float, default=50.0)
    p.add_argument("--leg2-pc", type=float, default=2500.0)
    p.add_argument("--target-gc-pc", type=float, default=400.0)
    p.add_argument("--split", type=float, default=0.5)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--projection", choices=["perspective", "fisheye"], default="perspective")
    p.add_argument("--fov-deg", type=float, default=90.0)
    p.add_argument("--look-transition-sec", type=float, default=2.0,
                   help="Seconds after the second leg starts for the camera to finish turning toward its target.")
    p.add_argument("--start-look-dir", help="Override initial look direction as x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--end-look-dir", help="Override final look direction as x,y,z in equatorial Cartesian coordinates.")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--pct", type=float, default=99.7)
    add_psf_cli_args(p)
    p.add_argument("--no-dipper-overlay", action="store_true", help="Disable Big Dipper guide lines in perspective mode.")
    p.add_argument("--overlay-width", type=int, default=1)
    p.add_argument("--save-hdr", action="store_true", help="Also keep 16-bit TIFF frames.")
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--no-mp4", action="store_true")
    return p


def config_from_args(args):
    frames = resolve_frame_count(args.frames, args.fps, args.duration)
    first_leg_dir = big_dipper_direction()
    second_leg_target = galactic_center_direction() * args.target_gc_pc + galactic_pole_direction() * args.leg2_pc
    positions, phase = shared_l_positions(
        frames,
        args.leg1_pc,
        args.leg2_pc,
        args.split,
        leg1_dir=first_leg_dir,
        leg2_target=second_leg_target,
    )
    start_dir = parse_triplet(args.start_look_dir) if args.start_look_dir else big_dipper_direction()
    end_dir = parse_triplet(args.end_look_dir) if args.end_look_dir else None
    look_phase = accelerated_look_phase(phase, frames, args.fps, args.split, args.look_transition_sec)
    look_dirs = (
        shared_l_look_dirs(frames, start_dir, end_dir, look_phase)
        if end_dir is not None
        else shared_l_look_at_dirs(positions, start_dir, galactic_center_direction() * args.target_gc_pc, look_phase)
    )
    cfg = {
        "width": args.width,
        "height": args.height,
        "frames": frames,
        "positions": positions,
        "look_dirs": look_dirs,
        "projection": args.projection,
        "fov_deg": args.fov_deg,
        "gamma": args.gamma,
        "pct": args.pct,
        "dipper_overlay": not args.no_dipper_overlay,
        "overlay_width": args.overlay_width,
    }
    cfg.update(psf_config_from_args(args))
    return cfg


def accelerated_look_phase(phase, frames, fps, split, transition_sec):
    split_index = max(1, min(frames - 1, int(round(frames * split)))) if frames > 1 else 1
    if transition_sec <= 0:
        out = np.zeros_like(phase)
        out[split_index:] = 1.0
        out[-1] = 1.0
        return out
    transition_frames = max(1.0, transition_sec * max(fps, 1))
    out = np.zeros_like(phase)
    idx = np.arange(len(phase))
    t = np.clip((idx - split_index) / transition_frames, 0.0, 1.0)
    out = 0.5 - 0.5 * np.cos(np.pi * t)
    out[:split_index] = 0.0
    out[-1] = 1.0
    return out


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)
    render_frames_parallel(args.data, args.frames_dir, cfg, render_forward_frame, args.workers, args.save_hdr)
    if not args.no_mp4:
        assemble_mp4(args.frames_dir, args.output, args.fps, args.crf)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
