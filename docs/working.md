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
