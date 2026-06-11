#!/usr/bin/env bash
# Serial ablation renderer. Activates venv INSIDE (nohup drops it otherwise).
# One python process at a time (OOM safety; --workers 16 max). 1080x1920 portrait.
set -euo pipefail
cd /Users/grapeot/co/knowledge_working/adhoc_jobs/gaia_allsky
source .venv/bin/activate

OUT=outputs/ablation
ASSETS=docs/assets
mkdir -p "$OUT"

# Common: hero tone for the dark-sky ladder + scale series
HERO="--target-sky 0.038 --target-white 2.6 --star-contrast 6 --chroma 1.8"
RES="--width 1080 --height 1920 --workers 16"
G20=data/raw/fov_g20_bsc5.npz

render() {
  # $1 = output basename (no ext); rest = render_fov args
  local name="$1"; shift
  echo "=== RENDER $name ==="
  if [ -f "$OUT/$name.png" ]; then
    echo "  PNG exists, skip render (reuse)"
  else
    /usr/bin/time -l python src/render_fov.py "$@" --out "$OUT/$name.png" 2>&1 | \
      grep -E "星，|wrote|signal_stretch|maximum resident|real " || true
  fi
  magick "$OUT/$name.png" -quality 90 "$ASSETS/$name.jpg"
  echo "--> $ASSETS/$name.jpg done"
}

# ---- Dark-sky ablation ladder (hero tone, g20_bsc5) ----
render ablation_1_naive      --data $G20 --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0   --faint-gain 1 --sat-over-sky 0 $RES
render ablation_3_faintgain  --data $G20 --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 0 $RES
render ablation_4_satbloom   --data $G20 --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 $RES
render ablation_5_full       --data $G20 --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 $RES

# ---- Scale-up depth series (hero tone; data/faint-gain vary) ----
render ablation_scale_g13gain --data data/raw/fov_g13.npz --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 3.8 --sat-over-sky 6 $RES
render ablation_scale_g13     --data data/raw/fov_g13.npz --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1   --sat-over-sky 6 $RES
render ablation_scale_g16     --data data/raw/fov_g16.npz --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1   --sat-over-sky 6 $RES
render ablation_scale_g18     --data data/raw/fov_g18.npz --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1   --sat-over-sky 6 $RES
render ablation_scale_g20     --data $G20                 --bortle 1 --value 0 $HERO --ext-threshold 0 \
        --psf-core-px 0.6 --faint-gain 1   --sat-over-sky 6 $RES

# ---- Weber pair at Bortle 7 (physics path tone, NOT hero) ----
WEBER="--target-sky 0.012 --target-white 1.0 --star-contrast 6 --chroma 1.8"
render ablation_weber_off --data $G20 --bortle 7 --value 0 $WEBER \
        --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --ext-threshold 0 $RES
render ablation_weber_on  --data $G20 --bortle 7 --value 0 $WEBER \
        --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --ext-threshold 0.04 --ext-softness 0.5 $RES

echo "ALL DONE"
