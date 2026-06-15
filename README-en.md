# Gaia All-Sky Star Experiment

Plot over a hundred million real stars from ESA's Gaia DR3 catalog back onto the celestial sphere—one point per star. No Milky Way texture overlay; the galaxy emerges naturally from stellar density alone.

[Live Preview](https://grapeot.github.io/gaia_allsky/)
| [Rendering Principles](https://grapeot.github.io/gaia_allsky/principles-en.html)
| [中文](README.md)

## What Is This

This project takes 1.8 billion real stars measured by the Gaia satellite and projects each one onto the screen at its catalog position, magnitude, and color index. No Milky Way texture or dust assets are overlaid. As you dial light pollution from pristine dark skies up to a major city center, the Milky Way's luminous band fades section by section—not a visual effect, but sky background brightness overwhelming the diffuse light contrast. Fly the viewpoint out of the Solar System: the Big Dipper falls apart within a few dozen light-years, yet the Milky Way's large-scale structure holds steady. Code, rendering parameters, and data processing scripts are fully public; every effect is reproducible.

## Quick Start

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest tests/ -q
```

Tests do not depend on the full Gaia dataset; a handful of Milky Way density integration tests are automatically skipped when the large catalog is absent.

## Data Acquisition

Neither catalog data nor rendered output is committed to Git. Official rendering uses Flatiron bulk mirror's Gaia DR3 shards (2044 gzip files, ~412 GiB total), locally filtered and cached as NPZ. See `docs/gaia_catalog_usage.md` for the full workflow.

For a lightweight experience, download the G<11 catalog cache (~1.25 million stars):

```bash
python src/fetch_gaia_allsky.py --gmax 11 --output data/raw/gaia_g11.npz
```

## Reproducing Results

Bortle 1–9 Milky Way fade-out sequence:

```bash
python src/render_bortle_eye_grid.py --bortles 1,2,3,4,5,6,7,8,9 --eye-deltas 0 --columns-per-row 3 --output outputs/bortle_scale.png
```

Fly-through video (forward perspective):

```bash
python src/render_big_dipper_video.py --width 2160 --height 2160 --duration 10 --fps 60 --workers 32
```

Panoramic video (VR equirectangular):

```bash
python src/render_vr_video.py --width 4096 --height 2048 --duration 10 --fps 60 --workers 32
```

Deep single-frame render (~616 million G<20 real stars):

```bash
python src/build_fov_deep_cache.py --gmax 20 --out data/raw/fov_g20.npz --workers 16
python src/render_fov.py --data data/raw/fov_g20.npz --out outputs/fov_g20.png --faint-gain 1.0 --workers 28
```

## Directory Structure

```
src/
  render_starmap.py              Magnitude & color index, all-sky projection, PSF convolution, tone mapping
  render_horizon.py              Horizontal coordinate transform and Bortle skyglow model
  render_bortle_eye_grid.py      Bortle × sensitivity comparison grid entry point
  render_3d.py                   Gaia parallax 3D reprojection
  render_big_dipper_video.py     Forward-perspective fly-through video
  render_vr_video.py             VR panoramic fly-through video
  render_fov.py                  Parallel single-frame rendering for hundred-million-star catalogs
  render_tan_wcs.py              TAN projection + WCS output, interfacing with HiPS tile pipeline
  video_common.py                Parallel per-frame rendering, ffmpeg compositing
  motion.py                      Shared L-shaped flight trajectory
  fetch_gaia_allsky.py           Gaia all-sky catalog acquisition (sharded by magnitude range)
  fetch_gaia_3d.py               Nearby 3D subset acquisition
  tone_iterate.py                Iterative tone mapping on a linear canvas in seconds
  build_fov_deep_cache.py        Parallel decompression of Flatiron shards to build deep catalog cache
docs/
  prd.md                         Scientific goals and success criteria
  rfc.md                         Rendering pipeline design document
  working.md                     Historical decisions and parameter tuning notes
  gaia_catalog_usage.md          Catalog acquisition and caching conventions
  bortle_skyglow.md              Bortle/SQM/NELM reference table
tests/
  test_render.py                 Physics, projection, motion, tone map, and CLI semantic tests
```

## Scientific Boundaries

This project pursues qualitative correctness and interpretability, not photometric-grade precision. The physical model is anchored layer by layer within publicly available data: magnitudes are converted to brightness via the Pogson formula, color indices are calibrated against the Pecaut & Mamajek main-sequence table into surface temperature, then integrated over a blackbody spectrum to obtain sRGB, with the Sun (G2V, 5772 K) anchored as neutral white. The display layer is strictly separated from the physics layer—stellar physical brightness is governed by formulas, while on-screen appearance is controlled by explicitly exposed display parameters that never leak into astrophysical constants.

Gaia parallax precision degrades with distance; only stars within a few thousand light-years of the Sun have reliable distances. In the fly-through video, the farther you travel, the sparser the stars become—that's not the edge of the universe, it's the edge of measurement. This project can demonstrate constellation breakup and local star-field reprojection, but cannot produce a true top-down view of the Milky Way—humanity does not yet have such a photograph.

The rendering introduces no nebula model, interstellar dust model, or Milky Way texture. The Milky Way's milky glow comes from the density distribution of countless faint stars; dark dust lanes come from genuine star-count deficits in those directions—all positive and negative information from the data itself.

The screen is an SDR device. The millions-to-one brightness ratio between the brightest real star and the faintest Milky Way texture must be compressed and mapped before it can be displayed. The image preserves the relative ordering of "which is brighter than which" but makes no guarantee of photometric proportionality in absolute brightness. Use these images for structure, trends, and comparison—they are reliable. Use them to measure absolute magnitudes—they are not.


