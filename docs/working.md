# Working Notes

## 2026-06-08: Split L-flight Video Into Two CLIs

Goal: keep the existing mixed `render_l_video.py` behavior intact, but add two separate render paths so viewers do not need to switch between VR/equirectangular and fisheye viewing modes inside one video.

Implemented:

- Added `src/video_common.py` for shared SDR video rendering helpers: data loading, easing, Big Dipper direction, parallel frame rendering, PNG/TIFF frame writing, and H.264 mp4 assembly.
- Added `src/render_vr_video.py` for a pure equirectangular VR flight along the galactic plane.
- Added `src/render_big_dipper_video.py` for a forward fisheye flight looking and flying toward the Big Dipper center.
- Added CLI flags for resolution, frame count, fps, worker count, output paths, CRF, optional 16-bit TIFF frame retention, and direction overrides.
- Kept frame directories as first-class outputs; ffmpeg runs only after frames are written.

Parallelization:

- The M3 Ultra host reports 32 CPU cores.
- The new CLIs default `--workers` to `os.cpu_count()` and were tested with `--workers 32`.
- Workers write frames directly to disk, avoiding large frame-array IPC back to the parent process. This matters for future 8K equirectangular rendering.

Low-resolution previews generated:

```bash
python src/render_vr_video.py \
  --width 640 --height 320 --frames 60 --fps 30 --workers 32 \
  --frames-dir outputs/vr_equirect_lowres_frames \
  --output outputs/vr_equirect_lowres.mp4

python src/render_big_dipper_video.py \
  --width 640 --height 640 --frames 60 --fps 30 --workers 32 \
  --frames-dir outputs/big_dipper_forward_lowres_frames \
  --output outputs/big_dipper_forward_lowres.mp4
```

Verification:

- `python -m pytest tests/ -q` -> 22 passed.
- `outputs/vr_equirect_lowres_frames/` contains 60 PNG frames.
- `outputs/big_dipper_forward_lowres_frames/` contains 60 PNG frames.
- `outputs/vr_equirect_lowres.mp4`: H.264, `yuv420p`, 640x320, 30 fps, 60 frames, 2 seconds.
- `outputs/big_dipper_forward_lowres.mp4`: H.264, `yuv420p`, 640x640, 30 fps, 60 frames, 2 seconds.

Notes for next render pass:

- Full VR target can be produced by increasing `render_vr_video.py` to `--width 8192 --height 4096` and using the desired frame count/fps.
- The forward version is currently fisheye because `render_3d.render_fisheye_lookdir` already exists. A rectilinear/perspective renderer would be a separate addition if a non-fisheye forward camera is preferred.
- Preview frames and videos remain under `outputs/`, which is gitignored.

## 2026-06-08: Correction - Shared Motion, Separate Projection

The first split had a conceptual mistake: it made the VR video and the Big Dipper forward video use different motion paths. The intended design is different. Both outputs should render the same L-shaped motion; only the camera projection differs.

Updated model:

- Added `src/motion.py` for the shared L-shaped path and direction interpolation.
- `render_vr_video.py` now renders the full L path: first along the galactic plane, then upward toward the galactic pole.
- `render_big_dipper_video.py` uses the same position sequence as VR.
- The forward camera starts by looking toward the Big Dipper center and smoothly tilts toward the galactic pole during the second leg.
- The forward renderer now defaults to full-frame `perspective`, so the preview is rectangular rather than a circular fisheye disk.
- `--projection fisheye` remains available for the old circular fisheye look.

Current CLI semantics:

```bash
python src/render_vr_video.py \
  --width 640 --height 320 --frames 60 --fps 30 --workers 32 \
  --leg1-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/vr_equirect_lowres_frames \
  --output outputs/vr_equirect_lowres.mp4

python src/render_big_dipper_video.py \
  --width 640 --height 640 --frames 60 --fps 30 --workers 32 \
  --leg1-pc 400 --leg2-pc 2500 --projection perspective \
  --frames-dir outputs/big_dipper_forward_lowres_frames \
  --output outputs/big_dipper_forward_lowres.mp4
```

Verification after correction:

- `python -m pytest tests/ -q` -> 24 passed.
- `outputs/vr_equirect_lowres_frames/` contains 60 PNG frames.
- `outputs/big_dipper_forward_lowres_frames/` contains 60 PNG frames.
- `outputs/vr_equirect_lowres.mp4`: H.264, `yuv420p`, 640x320, 30 fps, 60 frames, 2 seconds.
- `outputs/big_dipper_forward_lowres.mp4`: H.264, `yuv420p`, 640x640, 30 fps, 60 frames, 2 seconds.
- First-frame Big Dipper QA: all seven Big Dipper stars project inside the 640x640 perspective frame, roughly x=255-373 and y=288-345. If the asterism is hard to recognize visually, the issue is lack of constellation line/marker overlay, not camera pointing.

## 2026-06-08: Duration Flag and Forward Camera Correction

Two clarifications changed the CLI defaults.

First, spatial resolution and temporal resolution are separate controls. The CLIs now accept `--duration` so callers can say `--duration 10 --fps 60`; the program computes 600 frames internally. `--frames` remains available when exact frame count is preferred.

Second, the forward camera and first-leg motion should start toward the Big Dipper. The intended effect is that the familiar Big Dipper shape is initially obvious, then changes as the observer flies toward it and the nearby bright stars reproject. During the second leg, motion leaves the disk toward the galactic pole while the camera turns toward the galactic center. Updated defaults:

- first leg motion: Big Dipper center direction
- second leg motion: galactic pole direction
- start look direction: Big Dipper center direction
- end look direction: galactic center direction
- interpolation: smooth slerp driven by second-leg phase

Updated 10-second low-resolution preview commands:

```bash
python src/render_vr_video.py \
  --width 640 --height 320 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/vr_equirect_lowres_frames \
  --output outputs/vr_equirect_lowres.mp4

python src/render_big_dipper_video.py \
  --width 640 --height 640 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 400 --leg2-pc 2500 --projection perspective \
  --frames-dir outputs/big_dipper_forward_lowres_frames \
  --output outputs/big_dipper_forward_lowres.mp4
```

Verification after this change:

- `python -m pytest tests/ -q` -> 26 passed.

## 2026-06-08: Big Dipper Visibility and Look-Down Leg

Two issues showed up in the Big Dipper forward preview.

First, the Big Dipper was technically inside the first frame, but it was hard to recognize as asterism geometry. The projected first-frame star coordinates were roughly x=187-430 and y=255-371 after tightening FOV to 60 degrees. The fix is to draw a thin guide overlay connecting the seven Big Dipper stars. The overlay uses approximate 3D positions for the seven stars, so the connected shape is reprojected every frame and changes with the observer position.

Second, moving literally toward the Big Dipper conflicts with the “stay near the disk, then leave the disk and look down” story. The Big Dipper direction is already close to the galactic pole, so flying toward it moves strongly out of the disk before the second leg. The shared position path is therefore restored to the physically intended L path:

- first leg: move along the galactic center direction inside/near the disk
- second leg: move toward the galactic pole

The forward camera is independent of the first-leg motion direction:

- first leg look direction: Big Dipper center, with guide-line overlay
- second leg look direction: `-galactic_pole`, looking back/down toward the region being left behind

Updated behavior:

- `render_big_dipper_video.py` defaults to `--fov-deg 60`, stronger bloom, and Big Dipper guide lines.
- `--no-dipper-overlay` disables the guide lines.
- `outputs/big_dipper_first_frame_overlay.png` was generated as a quick QA image for first-frame asterism placement.

Verification:

- `python -m pytest tests/ -q` -> 27 passed.
- Low-resolution 10-second 60fps previews were regenerated for both VR and Big Dipper outputs.

## 2026-06-08: Big Dipper-First Trajectory and Softer Bloom

The forward preview should physically move toward the Big Dipper during the first leg, not merely look at it. The second leg should then head toward a point above the galactic center, with the camera turning toward the galactic center.

Updated shared position path:

- first leg target: `big_dipper_direction * leg1_pc`
- second leg target: `galactic_center_direction * leg1_pc + galactic_pole_direction * leg2_pc`
- both VR and forward videos use this same position sequence

Updated forward camera path:

- start look direction: Big Dipper center
- end look direction: galactic center
- interpolation: smooth slerp driven by second-leg phase

Visual tuning:

- Forward preview default FOV remains 60 degrees so the Big Dipper occupies a meaningful part of the frame.
- Bloom was reduced to `--bloom-strength 0.35 --bloom-sigma 3.0`, because the previous default was too large for the tighter perspective view.

Verification:

- `python -m pytest tests/ -q` -> 28 passed.

## 2026-06-08: Use Old Frame 68 as First-Leg Endpoint

The 400pc first leg flies past the useful Big Dipper deformation window. In the 10-second 60fps preview, frame 68 of the old 400pc first leg corresponds to about 48.9pc along the eased first-leg path. This is close to the desired state: the Big Dipper remains in frame, visibly deforms, and has not been overflown.

Updated defaults:

- `--leg1-pc 50`: first leg endpoint, toward Big Dipper
- `--target-gc-pc 400`: horizontal galactic-center component of the second-leg target
- `--leg2-pc 2500`: vertical galactic-pole component of the second-leg target

The second-leg target is now independent of `leg1_pc`:

```text
target = galactic_center_direction * target_gc_pc + galactic_pole_direction * leg2_pc
```

This keeps the first leg short enough for the Big Dipper shape study while still sending the second leg toward a meaningful point above the galactic-center direction.

Verification:

- `python -m pytest tests/ -q` -> 29 passed.
