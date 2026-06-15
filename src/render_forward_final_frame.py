"""Render the final frame of the non-VR forward flight as a still image."""
import argparse
import os

import numpy as np
from PIL import Image

import render_big_dipper_video as bdv
import video_common as vc


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=vc.DATA_DEFAULT)
    p.add_argument("--out", required=True)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--duration", type=float)
    p.add_argument("--leg1-pc", type=float, default=50.0)
    p.add_argument("--leg2-pc", type=float, default=2500.0)
    p.add_argument("--target-gc-pc", type=float, default=400.0)
    p.add_argument("--split", type=float, default=0.5)
    p.add_argument("--projection", choices=["perspective", "fisheye"], default="perspective")
    p.add_argument("--fov-deg", type=float, default=90.0)
    p.add_argument("--look-transition-sec", type=float, default=2.0)
    p.add_argument("--start-look-dir")
    p.add_argument("--end-look-dir")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--pct", type=float, default=99.7)
    vc.add_psf_cli_args(p)
    p.add_argument("--no-dipper-overlay", action="store_true")
    p.add_argument("--overlay-width", type=int, default=0)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = bdv.config_from_args(args)
    vc.init_worker(args.data, cfg)
    frame = vc.render_forward_frame(cfg["frames"] - 1)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    Image.fromarray((np.clip(frame, 0.0, 1.0) * 255).astype("uint8")).save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
