#!/bin/bash
# Per-order HiPS 渲染 pipeline —— 治"zoom-out 结构丢失"的根本解（见 docs/working.md 2026-06-14）。
#
# 为什么不能用普通 pipeline + hipsgen 池化：源是十亿点源（亚像素 delta 场）。hipsgen 把最高 order
# 瓦片 2×2 池化生成低 order，对 delta 场是抽样而非积分——密集星场和暗带都塌成噪点，zoom-out 看
# 不到"暗星织成的发光底 + 尘埃暗带"。这是采样定理问题，不是 tone/能量问题，换 Aladin v3 / hipsgen
# 任何池化模式都救不了（已调研确认）。
#
# 正确解：每个 Norder 从星表**各渲一次**，暗星 PSF 匹配该层像素尺度（≈1px），让暗星在每层都有
# 宽度、密集处重叠累加成连续底。等价于 CDS 给 Gaia DR3 做 HiPS 的 per-order 思路。
#
# 两阶段（可分别只跑）：
#   [render] 每 order：标定 tone → 渲该层 TAN 瓦片（psf≈1px，--resume 断点续传）。纯 Python。
#   [hipsgen] 每 order：hipsgen 重投影出该层 HEALPix 瓦片，**可并行**（各 order 独立）；只取对应
#             NorderK 拼进最终金字塔 + Allsky + index.html。java。
# tile 渲染最慢、可后台；hipsgen 是 java 重投影。两阶段解耦：后台只渲 tiles、空闲时再并行拼。
#
# Switch：
#   （默认）       端到端：render + hipsgen + 组装
#   --tiles-only  只渲 tiles（放后台跑；纯 Python 不碰 java）
#   --hipsgen-only 只对已渲 tiles 跑 hipsgen + 组装（CPU 空闲时并行拼）
#
# HiPS 各 order 像素分辨率（512px/tile，cell=58.63°/2^order）：
#   N3=51.5"  N4=25.8"  N5=12.9"  N6=6.44"  N7=3.22"  N8=1.61"(≈源分辨率，最高有意义层)
#
# 用法：
#   bash tools/render_per_order_pipeline.sh <tag> <npz> <lc> <bc> <half> <orders> [opts...]
#   bash tools/render_per_order_pipeline.sh <tag> <npz> --range <llo,lhi> <blo,bhi> <orders> [opts...]
# opts（任意顺序）：
#   --workers N      渲染并行进程数（默认 8；32 核机建议 30）
#   --hipsgen-par N  并行跑几个 order 的 hipsgen（默认 3）
#   --hipsgen-th N   每个 hipsgen 的线程数（默认 8）
#   --tiles-only | --hipsgen-only
#   --no-resume      渲染不续传（默认 resume：跳过已渲 tile）
# 例（心宿二 ±3° 端到端，30 进程）：
#   bash tools/render_per_order_pipeline.sh ant_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz \
#       351.95 15.06 3 "3 4 5 6 7 8" --workers 30
# 例（广州，先后台只渲 tiles）：
#   bash tools/render_per_order_pipeline.sh gz_po data/raw/fov_g20_bsc5_hpx6.npz \
#       --range -41,79 -31,43 "3 4 5 6 7 8" --workers 30 --tiles-only
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate
JAVA="${JAVA:-/opt/homebrew/opt/openjdk@11/bin/java}"
JAR=outputs/tmp_reference_hips/AladinBeta.jar

TAG="${1:?tag}"; NPZ="${2:?npz}"
# 位置参数：中心+half 或 --range llo,lhi blo,bhi
if [ "$3" = "--range" ]; then
  IFS=',' read LLO LHI <<< "$4"; IFS=',' read BLO BHI <<< "$5"
  LC=$(python3 -c "print(($LLO+$LHI)/2)"); BC=$(python3 -c "print(($BLO+$BHI)/2)")
  ORDERS="${6:?orders}"; HALF=0; shift 6
else
  LC="$3"; BC="$4"; HALF="${5:?half_deg}"; ORDERS="${6:?orders}"
  LLO=$(python3 -c "print($LC-$HALF)"); LHI=$(python3 -c "print($LC+$HALF)")
  BLO=$(python3 -c "print($BC-$HALF)"); BHI=$(python3 -c "print($BC+$HALF)")
  shift 6
fi

# 默认值 + 解析 opts
W=8; HPAR=3; HTH=8; DO_RENDER=1; DO_HIPSGEN=1; RESUME="--resume"; DARK_PSF=""
while [ $# -gt 0 ]; do
  case "$1" in
    --workers) W="$2"; shift 2;;
    --hipsgen-par) HPAR="$2"; shift 2;;
    --hipsgen-th) HTH="$2"; shift 2;;
    --tiles-only) DO_HIPSGEN=0; shift;;
    --hipsgen-only) DO_RENDER=0; shift;;
    --no-resume) RESUME=""; shift;;
    --dark-psf) DARK_PSF="$2"; shift 2;;   # 覆盖所有 order 的暗星 PSF（默认低层1.0/N8 0.6）
    *) echo "未知参数: $1"; exit 1;;
  esac
done

OUT="outputs/$TAG"; HIPS="$OUT/hips"; mkdir -p "$OUT"

# 各 order 的渲染参数（cell/分辨率推导）
order_params() {  # $1=order -> echo "TFOV TSIZE TSTEP PSF"
  python3 - "$1" "$HALF" "$DARK_PSF" <<'PY'
import sys
k=int(sys.argv[1]); half=float(sys.argv[2])
override=sys.argv[3] if len(sys.argv)>3 else ""
cell=58.6323/2**k; cdelt=cell/512.0       # 该层瓦片像素分辨率
fov = 1.5*cell if half<=0 else min(1.5*cell, 2*half+1.0)
size=int(round(fov/cdelt))
while size>4096: fov/=2; size=int(round(fov/cdelt))
size=max(size,256); step=fov*0.8
# 默认：N8 锐点 0.6、低层 1.0 织底。--dark-psf 覆盖所有层（实测 0.6 各层更锐且不丢底）。
psf = float(override) if override else (0.6 if k>=8 else 1.0)
print(f"{fov:.4f} {size} {step:.4f} {psf}")
PY
}

# ---- 阶段1：渲 tiles（每 order 标定 + 渲，--resume 断点续传）----
if [ "$DO_RENDER" = 1 ]; then
  for k in $ORDERS; do
    read TFOV TSIZE TSTEP PSF < <(order_params "$k")
    WK="$OUT/o$k"; mkdir -p "$WK/tiles"
    echo "=== [render] Norder$k: cdelt=$(python3 -c "print(f'{$TFOV*3600/$TSIZE:.2f}')")\"/px fov=$TFOV size=$TSIZE psf=$PSF W=$W ==="
    python src/calibrate_alltile_tone.py --data "$NPZ" \
      --tile-fov "$TFOV" --tile-size "$TSIZE" --value 6 --target-sky 0.020 \
      --star-contrast 4 --target-white 2.6 --psf-core-px "$PSF" --out "$WK/calib.json" 2>&1 | tail -1
    python src/render_tan_wcs.py --data "$NPZ" --out "$WK/tiles" --tiles \
      --l-range="$LLO,$LHI" --b-range="$BLO,$BHI" \
      --tile-fov "$TFOV" --tile-step "$TSTEP" --tile-size "$TSIZE" --psf-core-px "$PSF" \
      --workers "$W" --value 6 --calib "$WK/calib.json" $RESUME 2>&1 | tail -1
  done
  echo "TILES_DONE [$TAG]"
fi

# ---- 阶段2：hipsgen 各 order（可并行）+ 组装金字塔 ----
if [ "$DO_HIPSGEN" = 1 ]; then
  hipsgen_one() {
    local k="$1"
    [ -d "$OUT/o$k/tiles" ] || { echo "  ⚠ o$k/tiles 不存在，跳过"; return; }
    trash "$OUT/o$k/hips" 2>/dev/null || true
    "$JAVA" -Xmx8g -jar "$JAR" -hipsgen in="$OUT/o$k/tiles" out="$OUT/o$k/hips" \
      color=jpeg hips_order="$k" maxThread="$HTH" creator_did=DuckBro obs_title="$TAG" \
      "target=$LC $BC" INDEX TILES > "$OUT/o$k.hipsgen.log" 2>&1
    echo "  [hipsgen] N$k: $(grep 'THE END' "$OUT/o$k.hipsgen.log" | grep -oE 'done in [^)]*')"
  }
  echo "=== [hipsgen] 并行 $HPAR×$HTH 线程 ==="
  running=0
  for k in $ORDERS; do
    hipsgen_one "$k" &
    running=$((running+1))
    [ "$running" -ge "$HPAR" ] && { wait -n 2>/dev/null || wait; running=$((running-1)); }
  done
  wait

  echo "=== 组装金字塔：各 order 取自己的 NorderK ==="
  trash "$HIPS" 2>/dev/null || true; mkdir -p "$HIPS"
  for k in $ORDERS; do
    [ -d "$OUT/o$k/hips/Norder$k" ] && cp -R "$OUT/o$k/hips/Norder$k" "$HIPS/Norder$k" && echo "  Norder$k ← o$k"
  done
  HI=$(echo $ORDERS | tr ' ' '\n' | sort -n | tail -1)
  LO=$(echo $ORDERS | tr ' ' '\n' | sort -n | head -1)
  cp "$OUT/o$HI/hips/properties" "$HIPS/properties" 2>/dev/null || true
  cp "$OUT/o$HI/hips/Moc.fits" "$HIPS/Moc.fits" 2>/dev/null || true
  python src/rebuild_allsky_hires.py --hips "$HIPS" --order "$LO" 2>&1 | tail -1 || true
  cp skills/hips_landing_page.html "$HIPS/index.html" 2>/dev/null || true
  echo "PER_ORDER_DONE → $HIPS （orders: $ORDERS）"
fi
