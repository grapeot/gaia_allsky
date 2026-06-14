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
# 实现：对每个 order，①按该层分辨率标定 tone ②渲该层 TAN 瓦片（psf≈1px）③hipsgen 重投影出该层
# HEALPix 瓦片（只取它生成的对应 NorderN，下层池化产物丢弃）。最后把各层对应 order 拼进一个金字塔
# + Allsky + index.html，Aladin Lite 可看。
#
# HiPS 各 order 像素分辨率（512px/tile，cell=58.63°/2^order）：
#   N3=51.5"  N4=25.8"  N5=12.9"  N6=6.44"  N7=3.22"  N8=1.61"(≈源分辨率，最高有意义层)
#
# 用法：
#   bash tools/render_per_order_pipeline.sh <tag> <npz> <lc> <bc> <half_deg> <orders> [workers]
# 例（心宿二 ±3°，N3–N8）：
#   bash tools/render_per_order_pipeline.sh ant_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz \
#       351.95 15.06 3 "3 4 5 6 7 8" 8
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate
JAVA="${JAVA:-/opt/homebrew/opt/openjdk@11/bin/java}"
JAR=outputs/tmp_reference_hips/AladinBeta.jar

TAG="${1:?tag}"; NPZ="${2:?npz}"; LC="${3:?lc}"; BC="${4:?bc}"
HALF="${5:?half_deg（半边长，±度）}"; ORDERS="${6:?orders，空格分隔如 \"3 4 5 6 7 8\"}"
W="${7:-8}"

OUT="outputs/$TAG"; HIPS="$OUT/hips"
mkdir -p "$OUT"
# 天区范围（±HALF），含 1° 余量让 hipsgen 重投影不缺边
LLO=$(python3 -c "print($LC-$HALF)"); LHI=$(python3 -c "print($LC+$HALF)")
BLO=$(python3 -c "print($BC-$HALF)"); BHI=$(python3 -c "print($BC+$HALF)")

for k in $ORDERS; do
  # 该 order 瓦片像素分辨率 = 58.6323/2^k/512 度/px；TAN 方图 cdelt 取等值即不被 hipsgen 再降采样。
  # 方图 tile-fov 取 ~1.2×cell（够大省 tile 数），size = fov/cdelt，step=0.8×fov 重叠无缝。
  read TFOV TSIZE TSTEP PSF < <(python3 - "$k" "$HALF" <<'PY'
import sys
k=int(sys.argv[1]); half=float(sys.argv[2])
cell=58.6323/2**k                      # Norder-k cell 边长（度）
cdelt=cell/512.0                       # 该层瓦片像素分辨率（度/px）
# 方图选成覆盖整块的单张（size 不爆）或网格。这里 fov 取 min(1.5×cell, 2×half+1)，size=fov/cdelt
fov=min(1.5*cell, 2*half+1.0)
size=int(round(fov/cdelt))
# size 太大（>4096）就缩 fov 多张拼；太小（<256）补到 256
while size>4096: fov/=2; size=int(round(fov/cdelt))
size=max(size,256)
step=fov*0.8
psf=0.6 if k>=8 else 1.0               # 最高层锐点保单星；低层 1px 织底
print(f"{fov:.4f} {size} {step:.4f} {psf}")
PY
)
  WK="$OUT/o$k"; mkdir -p "$WK/tiles"
  echo "=== Norder$k: cdelt=$(python3 -c "print(f'{$TFOV*3600/$TSIZE:.2f}')")\"/px fov=$TFOV° size=$TSIZE psf=$PSF ==="
  echo "--- [a] 标定 N$k tone ---"
  python src/calibrate_alltile_tone.py --data "$NPZ" \
    --tile-fov "$TFOV" --tile-size "$TSIZE" --value 6 --target-sky 0.020 \
    --star-contrast 4 --target-white 2.6 --psf-core-px "$PSF" --out "$WK/calib.json" 2>&1 | tail -1
  echo "--- [b] 渲 N$k TAN 瓦片 ---"
  python src/render_tan_wcs.py --data "$NPZ" --out "$WK/tiles" --tiles \
    --l-range="$LLO,$LHI" --b-range="$BLO,$BHI" \
    --tile-fov "$TFOV" --tile-step "$TSTEP" --tile-size "$TSIZE" --psf-core-px "$PSF" \
    --workers "$W" --value 6 --calib "$WK/calib.json" 2>&1 | tail -1
  echo "--- [c] hipsgen 出 N$k 层 ---"
  trash "$WK/hips" 2>/dev/null || true
  "$JAVA" -Xmx8g -jar "$JAR" -hipsgen in="$WK/tiles" out="$WK/hips" color=jpeg \
    hips_order="$k" maxThread="$W" creator_did=DuckBro obs_title="$TAG" \
    "target=$LC $BC" 2>&1 | tail -1
done

# 拼最终金字塔：每个 order 只取它自己渲的那层 NorderK，组进 $HIPS
echo "=== 组装金字塔：各 order 取自己的 NorderK ==="
trash "$HIPS" 2>/dev/null || true; mkdir -p "$HIPS"
for k in $ORDERS; do
  if [ -d "$OUT/o$k/hips/Norder$k" ]; then
    cp -R "$OUT/o$k/hips/Norder$k" "$HIPS/Norder$k"
    echo "  Norder$k ← o$k"
  fi
done
# properties / Allsky / index.html 用最高 order 的那套 hipsgen 产物做底，再补全各层
HI=$(echo $ORDERS | tr ' ' '\n' | sort -n | tail -1)
LO=$(echo $ORDERS | tr ' ' '\n' | sort -n | head -1)
cp "$OUT/o$HI/hips/properties" "$HIPS/properties" 2>/dev/null || true
cp "$OUT/o$HI/hips/Moc.fits" "$HIPS/Moc.fits" 2>/dev/null || true
# Allsky 从最低 order 重建（zoom-out 预览要清晰，见 rebuild_allsky_hires）
python src/rebuild_allsky_hires.py --hips "$HIPS" --order "$LO" 2>&1 | tail -1 || true
cp skills/hips_landing_page.html "$HIPS/index.html" 2>/dev/null || true
echo "PER_ORDER_DONE → $HIPS （orders: $ORDERS）"
