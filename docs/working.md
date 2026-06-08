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

## 2026-06-08: Fix Final Camera to Look At the Disk Target

The sparse final Big Dipper forward view came from a camera semantics bug. The code used a fixed “galactic center direction” vector as the final look direction. From a point above the disk, that means looking roughly parallel to the disk from an elevated position, not looking at the disk/galaxy target below.

Diagnostic star counts in the final 60-degree FOV:

- fixed galactic-center direction: about 1,212 Gaia stars
- look-at disk target (`galactic_center_direction * target_gc_pc`): about 1,088,974 Gaia stars

Updated default camera behavior:

- first leg: look toward Big Dipper center
- second leg: smoothly transition to looking at the fixed disk target point
- `--end-look-dir` remains available as an explicit override for fixed direction experiments

Verification:

- `python -m pytest tests/ -q` -> 29 passed.

## 2026-06-08: Faster Camera Turn and Wider Forward FOV

The second leg looked better when the camera turn completed early, then held on the disk target while the observer kept moving. The forward CLI now separates camera turn timing from position interpolation:

- `--look-transition-sec 2.0` by default: after the second leg starts, the camera finishes turning in 2 seconds of video time.
- Position motion still eases over the full second leg.
- Look phase is computed from frame time, not from position easing phase.

The forward perspective FOV was widened by 50%:

- old default: `--fov-deg 60`
- new default: `--fov-deg 90`

Verification:

- `python -m pytest tests/ -q` -> 30 passed.

## 2026-06-08: Bortle x Eye Sensitivity Grid

Existing outputs already covered the two one-dimensional knobs:

- `outputs/knob_light_pollution.png`: light pollution sweep
- `outputs/knob_eye_sensitivity.png`: eye sensitivity sweep

What was missing was the combined comparison requested for Bortle 1 vs Bortle 6 under different human-eye sensitivities. Added `src/render_bortle_eye_grid.py`.

The first version used a horizon equirectangular projection, which is still a 360° x 180° all-sky map. That does not match the intended human-view simulation. The default is now a low-latitude Guangzhou ground-level sky window centered near galactic-center culmination, so the Milky Way is higher and more recognizable than in Beijing.

The normalization also changed. A direct percentile normalization makes light pollution look like “the whole image gets brighter.” Human vision and cameras adapt to the background. The default is now `--normalization sky_median`, which maps each panel’s median sky brightness to a stable gray level. This keeps the sky background comparable while making stars and the Milky Way lose contrast under Bortle 6.

Default adapted-vision grid:

- rows: Bortle 1 and Bortle 6
- columns: sensitivity cost +0mag, +2mag, +4mag
- panel labels include computed NELM; NELM is an output, not a CLI input
- projection: Guangzhou horizon-window view, horizon on the lower edge
- normalization: median sky adaptation
- output: `outputs/knob_bortle_eye_grid.png`

Command used:

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,6 \
  --eye-deltas 0,2,4 \
  --output outputs/knob_bortle_eye_grid.png
```

Verification:

- `python -m pytest tests/ -q` -> 35 passed.

## 2026-06-08: Horizon-Bottom View and Highlight Compression

The wide-angle perspective view still felt clipped because the horizon was not a stable visual baseline. The default projection for `render_bortle_eye_grid.py` is now `horizon_window`, implemented as a rectilinear camera rather than a linear az/alt unwrap:

- camera azimuth: centered on the galactic-center azimuth
- bottom-center ray: horizon
- vertical FOV: `--max-alt-deg`
- horizontal FOV: `--az-width-deg`

This makes the bottom edge a horizontal horizon line and the image read as “standing in Guangzhou, looking upward,” not as a VR all-sky unwrap.

The tone mapping now also compresses highlights after median sky adaptation:

- median sky adaptation handles background brightness adaptation
- `--white-pct 99.5` maps a high percentile to white
- a small fraction of saturated pixels is allowed, but large overexposed regions are avoided

Verification:

- `python -m pytest tests/ -q` -> 37 passed.
- `outputs/knob_bortle_eye_grid.png` regenerated with the horizon-bottom projection.

## 2026-06-08: Portrait Bortle Grid Defaults

The Bortle comparison grid is intended for a vertical “standing under the sky” read, not a horizontal strip. Updated defaults:

- panel size: `1080 x 1920`
- full 3-column x 2-row grid: `3240 x 3840`
- full 3-column x 3-row Bortle scale grid: `3240 x 5760`
- horizontal FOV: `90°`
- vertical FOV: `75°`

`outputs/knob_bortle_eye_grid.png` is the primary visual/subjective comparison. The SNR mode remains a debug/sanity-check path in the CLI, but `outputs/knob_bortle_exposure_snr_grid.png` is no longer a formal output.

## 2026-06-08: Rectilinear Horizon Camera

The focused Bortle scale view had the right normalization and framing, but the “horizon window” projection was still a linear azimuth/altitude map. That is not what a camera or eye sees. The renderer now uses a rectilinear perspective camera for `horizon_window`:

- horizontal FOV defaults to `90°`
- vertical FOV defaults to `75°`
- the bottom-center ray is the horizon
- the camera is centered on the galactic-center azimuth

The temporary QA overlay with the galactic plane curve was useful for projection validation, but is no longer a formal output. Formal outputs are unannotated.

## 2026-06-08: Sky-Limited SNR Mode

The adapted visual grid can be misleading if interpreted as “long exposure can overcome light pollution.” It models eye/camera adaptation for display, not detection SNR. A brighter sky contributes Poisson shot noise. Source signal grows linearly with exposure, but noise grows as the square root of source plus sky background:

```text
SNR = source * exposure / sqrt(source * exposure + sky * exposure + read_noise^2)
```

This means longer exposure helps, but bright sky still imposes a penalty. Under the same total exposure, Bortle 6 remains worse than Bortle 1; recovering the same SNR requires disproportionately more exposure and may still run into dynamic range, gradients, and processing limits.

Added `--mode snr` to `render_bortle_eye_grid.py` as a debug mode. It is not a formal deliverable because it is a detectability map, not a visual appearance map.

Command used:

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,6 \
  --exposures 1,10,100 \
  --mode snr \
  --normalization percentile \
  --output outputs/knob_bortle_exposure_snr_grid.png
```

Here the column values are exposure multipliers. Use this only for physics sanity checks; the formal visual output is `outputs/knob_bortle_eye_grid.png`.

## 2026-06-08: Guangzhou View and Bortle 1-9 Scale

The Beijing view puts the galactic center too low, so the grid does not read strongly as Milky Way. Defaults now use Guangzhou latitude (`--lat-deg 23.13`) with galactic-center culmination LST. This places the galactic center around 39 degrees altitude.

Added a Bortle 1-9 sequence using the same horizon-window view:

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,2,3,4,5,6,7,8,9 \
  --eye-deltas 0 \
  --columns-per-row 3 \
  --output outputs/knob_bortle_scale_grid.png
```

This is a 3x3 grid showing the Milky Way fading as skyglow increases.

## 2026-06-08: Darker Background Normalization

After increasing output resolution, the old sky adaptation target made even Bortle 1 look gray. The cause was not resolution by itself: the adapted sky level was too high, and full-image median could be affected by large Milky Way/star coverage.

Updated normalization:

- `--target-sky` default changed from `0.12` to `0.03` in linear RGB-channel units.
- Background estimate changed from image median to a low percentile, default `--sky-pct 25`.
- Highlight compression still uses `--white-pct 99.5`, but it no longer rescales the whole image by the white point.

Measured high-resolution output after the change:

- Bortle scale grid panel p25 RGB-sum is stable around `0.365`.
- Bortle 1 no longer has the previous gray background.
- Higher median in the darkest, highest-sensitivity panels comes from visible Milky Way/star coverage, not from sky background drift.

## 2026-06-08: Restore Star Contrast After Darker Adaptation

Lowering `--target-sky` fixed the gray background, but it also made stars and the Milky Way too dim because the whole image was scaled down together. The tone map now separates the adapted sky floor from signal above the sky:

- estimate sky background with `--sky-pct 25`
- map that background to `--target-sky 0.03`
- boost signal above the background with `--star-contrast 4.0`
- then apply highlight compression with `--white-pct 99.5`

Measured high-resolution output after this change:

- Bortle scale panel p25 RGB-sum stays around `0.365`, so the sky floor remains stable.
- Stars and Milky Way structure regain contrast above the adapted sky.
- The most extreme dark-sky +4mag sensitivity panel can saturate some highlights; lower `--star-contrast` to 3 if a softer presentation is desired.

Verification:

- `python -m pytest tests/ -q` -> 39 passed.

## 2026-06-08: Anchor Visual Brightness to Empirical NELM

The previous adapted visual path still had too much display tuning in the physical layer. The biggest diagnostic was `limiting_mag_for_sky()`: with the old sky/SNR approximation, Bortle 1 through Bortle 9 only moved from about 4.50 to 4.24 mag. That is far from the empirical Bortle naked-eye limiting magnitude scale, where Bortle 1 is around 7.6-8.0 and Bortle 6 is around 5.1-5.5.

The formal visual mode now uses an explicit empirical Bortle/NELM table as its visibility anchor:

```text
B1 7.8, B2 7.3, B3 6.8, B4 6.3, B5 5.8, B6 5.3, B7 4.8, B8 4.3, B9 4.0
```

For each panel:

```text
effective_nelm = empirical_bortle_nelm + sensitivity_delta_mag
```

A star at `effective_nelm` is assigned a fixed point-source contrast relative to the current skyglow (`--limiting-contrast`, default 0.5). Brighter stars follow Pogson scaling relative to that limit. This removes the previous double-counting path where `+2mag` both shifted the limiting magnitude and multiplied the rendered star signal by a separate gain.

A quick sweep showed that 0.08 made the Bortle 1 panel nearly indistinguishable from the adapted sky floor. That was not caused by `--white-pct 99.5`: the high percentiles were too low before highlight compression became relevant. Raising the limiting contrast to 0.5 keeps the empirical NELM anchor while making the Bortle 1 Milky Way visible in SDR.

Display tone mapping remains separate from detectability physics. `--target-sky`, `--sky-pct`, `--star-contrast`, and `--white-pct` are still display controls for mapping the physically anchored canvas into SDR. The scientific claim is therefore narrower and cleaner: the relative star/Milky-Way visibility is anchored to empirical NELM, while final contrast is an SDR rendering choice.

One more display-layer issue showed up in portal preview: a physically anchored 1px star field can look empty after the full 3240px-wide grid is downscaled in the UI. That is especially bad for the Milky Way, which should read as extended low-frequency structure rather than isolated bright pixels. A single wide PSF fixed the low-frequency glow but made bright stars too soft and crushed the histogram into a narrow range. The adapted visual mode now uses two layers:

- `--point-psf-px 1.0`: sharp point-source layer for bright stars.
- `--psf-px 6.0 --diffuse-strength 1.0`: wide diffuse layer for Milky-Way glow and portal/downscaled previews.

This keeps the NELM/star brightness anchor intact while preserving both point stars and the extended Milky Way band.

NELM semantics also need to be explicit. NELM is not a fixed hardware spec for the eye. It is the naked-eye limiting magnitude under a given sky background. The often quoted “6th magnitude naked-eye limit” is a rough normal/dark-sky convention, not the Bortle 1 maximum. Empirical Bortle tables put Bortle 1 around 7.6-8.0, and Bortle 5 around 5.6-6.0. Therefore `Bortle 1  NELM~7.8` and `Bortle 5  NELM~5.8` are internally consistent.

The next issue was histogram usage. A Bortle 1 single panel with the old highlight compression had RGB-sum p99.5 around 128/765, wasting most SDR dynamic range. The fix is reference-based display stretching:

1. Adapt each panel's sky floor independently, so the background remains comparable.
2. Use a shared reference panel to compute a single signal stretch from `--white-pct` to `--target-white`.
3. Apply that same stretch to every panel, so the reference uses the display range while other panels are not independently pulled up to white.

Measured `outputs/knob_bortle_scale_grid.png` after the reference stretch:

The final defaults are deliberately conservative:

- `--target-white 2.0`, not 3.0, so Bortle 1 is readable without looking like a long-exposure photo.
- `--diffuse-strength 0.33`, one third of the initial wide-PSF bloom layer.
- `--reference-mode brightest` for the sensitivity grid, so Bortle 1/+4mag is the display reference and the high-sensitivity column does not overexpose.
- `--reference-bortle 1 --reference-value 2` for the Bortle scale grid, so Bortle 1/+0mag is rendered with a display stretch similar to the more legible Bortle 1/+2mag panel in `knob_bortle_eye_grid.png`.

Measured `outputs/knob_bortle_scale_grid.png` after the final reference stretch:

```text
Bortle 1 RGB-sum p25/50/95/99.5:  93 / 102 / 162 / 286
Bortle 6 RGB-sum p25/50/95/99.5:  93 /  98 / 141 / 262
Bortle 9 RGB-sum p25/50/95/99.5:  93 /  93 /  96 / 110
```

Measured `outputs/knob_bortle_eye_grid.png` after the final reference stretch:

```text
Bortle 1 +0mag RGB-sum p25/50/95/99.5:  93 /  94 / 109 / 150
Bortle 1 +2mag RGB-sum p25/50/95/99.5:  93 / 102 / 166 / 295
Bortle 1 +4mag RGB-sum p25/50/95/99.5:  96 / 137 / 326 / 620
Bortle 6 +0mag RGB-sum p25/50/95/99.5:  93 /  93 /  95 / 103
Bortle 6 +4mag RGB-sum p25/50/95/99.5:  93 /  99 / 144 / 273
```

This is the intended split: the display reference uses SDR dynamic range, while light pollution and lower sensitivity still suppress high percentiles instead of being hidden by per-panel normalization.

Verification:

- `python -m pytest tests/ -q` -> 45 passed.
- Regenerated `outputs/knob_bortle_eye_grid.png` and `outputs/knob_bortle_scale_grid.png` with the NELM-anchored defaults.

## 2026-06-08: Final Bortle Grid Tuning and Hi-Res Videos

Final visual-grid changes after manual inspection:

- Reduced wide-PSF bloom strength from 1.0 to 0.33.
- Added explicit reference overrides: `--reference-bortle` and `--reference-value`.
- Kept `--reference-mode brightest` as the default for sensitivity grids, so the brightest/highest-sensitivity panel calibrates shared stretch.
- Rendered `outputs/knob_bortle_scale_grid.png` with `--reference-bortle 1 --reference-value 2`, matching the useful Bortle 1/+2mag look from `outputs/knob_bortle_eye_grid.png` while still keeping Bortle 7-9 dark.

Final commands:

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,6 \
  --eye-deltas 0,2,4 \
  --output outputs/knob_bortle_eye_grid.png

python src/render_bortle_eye_grid.py \
  --bortles 1,2,3,4,5,6,7,8,9 \
  --eye-deltas 0 \
  --columns-per-row 3 \
  --reference-bortle 1 \
  --reference-value 2 \
  --output outputs/knob_bortle_scale_grid.png
```

High-resolution video outputs were also generated:

```bash
python src/render_vr_video.py \
  --width 4096 --height 2048 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/vr_equirect_hires_frames \
  --output outputs/vr_equirect_hires.mp4

python src/render_big_dipper_video.py \
  --width 2160 --height 2160 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/big_dipper_forward_hires_frames \
  --output outputs/big_dipper_forward_hires.mp4
```

Verification:

- `python -m pytest tests/ -q` -> 48 passed.
- `outputs/vr_equirect_hires.mp4`: H.264, `yuv420p`, 4096x2048, 60fps, 600 frames.
- `outputs/big_dipper_forward_hires.mp4`: H.264, `yuv420p`, 2160x2160, 60fps, 600 frames.
