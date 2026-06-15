#!/usr/bin/env bash
# Serial ablation renderer. Activates venv INSIDE (nohup drops it otherwise).
# One python process at a time (OOM safety; --workers 16 max). 1080x1920 portrait.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
source .venv/bin/activate

OUT=outputs/ablation
ARTICLE_OUT=outputs/ablation_article
ASSETS=docs/assets
mkdir -p "$OUT"
mkdir -p "$ARTICLE_OUT"

# Common: hero tone for the dark-sky ladder + scale series
HERO="--target-sky 0.038 --target-white 2.6 --star-contrast 6 --chroma 1.8"
RES="--width 1080 --height 1920 --workers 16"
G11=data/raw/fov_g11.npz
G13=data/raw/fov_g13.npz
G16=data/raw/fov_g16.npz
G18=data/raw/fov_g18.npz
G20=data/raw/fov_g20_bsc5.npz

require_data() {
  local missing=0
  for path in "$@"; do
    if [ ! -f "$path" ]; then
      echo "missing required data cache: $path" >&2
      missing=1
    fi
  done
  if [ "$missing" -ne 0 ]; then
    exit 1
  fi
}

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

render_article() {
  require_data "$G20"

  # ---- 公众号主叙事：每一步只打开一个机制 ----
  OUT="$ARTICLE_OUT" render article_ablation_1_g11_naive \
          --data "$G20" --g-max 11 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0 --faint-gain 1 --sat-over-sky 0 --color-mode legacy-bv $RES
  OUT="$ARTICLE_OUT" render article_ablation_2_g11_bloom_legacy_color \
          --data "$G20" --g-max 11 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --color-mode legacy-bv $RES
  OUT="$ARTICLE_OUT" render article_ablation_3_g13_gain_legacy_color \
          --data "$G20" --g-max 13 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 3.8 --sat-over-sky 6 --color-mode legacy-bv $RES
  OUT="$ARTICLE_OUT" render article_ablation_4_g13_gain_color_calibrated \
          --data "$G20" --g-max 13 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 3.8 --sat-over-sky 6 --color-mode calibrated $RES

  # ---- 数据深度：同一显示模型，只换星表深度 ----
  OUT="$ARTICLE_OUT" render article_scale_g13 \
          --data "$G20" --g-max 13 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --color-mode calibrated $RES
  OUT="$ARTICLE_OUT" render article_scale_g16 \
          --data "$G20" --g-max 16 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --color-mode calibrated $RES
  OUT="$ARTICLE_OUT" render article_scale_g18 \
          --data "$G20" --g-max 18 --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --color-mode calibrated $RES
  OUT="$ARTICLE_OUT" render article_scale_g20_bsc5 \
          --data "$G20" --bortle 1 --value 0 $HERO --ext-threshold 0 \
          --psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6 --color-mode calibrated $RES

  # ---- 光污染：Bortle 7 下 Weber 阈值开关，必须走 sweep 路径 ----
  render_weber_sweep article_weber_off_b7 0
  render_weber_sweep article_weber_on_b7 0.04

  # ---- 三维边界：非 VR 前向飞行视频最后一帧 ----
  render_forward_final_frame
}

render_weber_sweep() {
  local name="$1"
  local threshold="$2"
  local sweep_dir="$ARTICLE_OUT/_${name}_sweep"
  local png="$sweep_dir/bortle_7.png"
  require_data "$G20"
  echo "=== SWEEP $name ==="
  if [ -f "$png" ]; then
    echo "  sweep PNG exists, skip render (reuse)"
  else
    mkdir -p "$sweep_dir"
    /usr/bin/time -l python src/render_fov.py \
      --data "$G20" --out "$ARTICLE_OUT/_unused_${name}.png" \
      --faint-gain 1 --target-white 1.0 --target-sky 0.012 \
      --star-contrast 6 --chroma 1.8 --workers 16 \
      --scene-ref-bortle 1 --width 1080 --height 1920 \
      --psf-core-px 0.6 --sat-over-sky 6 \
      --ext-threshold "$threshold" --ext-softness 0.5 \
      --sweep-bortles 7 --sweep-out-dir "$sweep_dir" 2>&1 | \
      grep -E "星，|wrote|signal_stretch|sweep|maximum resident|real " || true
  fi
  magick "$png" -quality 90 "$ASSETS/$name.jpg"
  echo "--> $ASSETS/$name.jpg done"
}

render_forward_final_frame() {
  local name="article_forward_final_frame"
  local png="$ARTICLE_OUT/$name.png"
  require_data data/raw/gaia_3d_deep_g13.npz
  echo "=== RENDER $name ==="
  if [ -f "$png" ]; then
    echo "  PNG exists, skip render (reuse)"
  else
    /usr/bin/time -l python src/render_forward_final_frame.py \
      --data data/raw/gaia_3d_deep_g13.npz \
      --out "$png" \
      --width 1080 --height 1080 --frames 300 --fps 60 \
      --no-dipper-overlay 2>&1 | \
      grep -E "wrote|maximum resident|real " || true
  fi
  magick "$png" -quality 90 "$ASSETS/$name.jpg"
  echo "--> $ASSETS/$name.jpg done"
}

render_principles() {
  require_data "$G13" "$G16" "$G18" "$G20"

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
}

MODE="${1:-article}"
case "$MODE" in
  article)
    render_article
    ;;
  principles)
    render_principles
    ;;
  *)
    echo "usage: $0 [article|principles]" >&2
    exit 2
    ;;
esac

echo "ALL DONE ($MODE)"
