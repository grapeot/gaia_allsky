#!/bin/bash
# tile-size sweep：固定 N8 分辨率(1.61"/px)，扫 TAN 方图 size，量 render+hipsgen end-to-end。
# 找 hipsgen 的甜点——size 小则源 tile 多→hipsgen 慢；size 大则单图重、并行差。凹函数。
# 心宿二 ±10°（20°×20°，现实规模）。
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate
JAVA="${JAVA:-/opt/homebrew/opt/openjdk@11/bin/java}"
JAR="${JAR:-outputs/tmp_reference_hips/AladinBeta_v12.jar}"
NPZ="${NPZ:-data/raw/fov_g20_bsc5_hpx6.npz}"
W="${W:-30}"; HTH="${HTH:-30}"; XMX="${XMX:-256g}"
LC=351.95; BC=15.06; LLO=341.95; LHI=361.95; BLO=5.06; BHI=25.06
CDELT=0.000447139  # N8: 1.6097"/px in deg

echo "PROFILE sweep machine cpu=$(python3 -c 'import os;print(os.cpu_count())') workers=$W xmx=$XMX npz=$(basename $NPZ)"
for SIZE in 768 1536 2048 3072 4096; do
  FOV=$(python3 -c "print($SIZE*$CDELT)")
  WK=outputs/sweep_n8_$SIZE
  trash "$WK" 2>/dev/null || rm -rf "$WK"; mkdir -p "$WK/tiles"
  # 标定（该 size）
  python src/calibrate_alltile_tone.py --data "$NPZ" --tile-fov "$FOV" --tile-size "$SIZE" \
    --value 6 --target-sky 0.020 --star-contrast 4 --target-white 2.6 --psf-core-px 0.6 \
    --out "$WK/calib.json" >&2
  # render（计时）
  S=$(python3 -c "import time;print(time.time())")
  python src/render_tan_wcs.py --data "$NPZ" --out "$WK/tiles" --tiles \
    --l-range="$LLO,$LHI" --b-range="$BLO,$BHI" --tile-fov "$FOV" --tile-step "$FOV" \
    --tile-size "$SIZE" --psf-core-px 0.6 --workers "$W" --value 6 --calib "$WK/calib.json" >&2
  E=$(python3 -c "import time;print(time.time())")
  NT=$(find "$WK/tiles" -name "*.png" | wc -l | tr -d ' ')
  RT=$(python3 -c "print(f'{$E-$S:.1f}')")
  # hipsgen（计时，minOrder=order 单层）
  S2=$(python3 -c "import time;print(time.time())")
  "$JAVA" -Xmx"$XMX" -jar "$JAR" -hipsgen in="$WK/tiles" out="$WK/hips" color=jpeg \
    minOrder=8 order=8 maxThread="$HTH" creator_did=D obs_title=sw "target=$LC $BC" \
    INDEX TILES > "$WK/hipsgen.log" 2>&1
  E2=$(python3 -c "import time;print(time.time())")
  HT_=$(python3 -c "print(f'{$E2-$S2:.1f}')")
  TOT=$(python3 -c "print(f'{$E2-$S:.1f}')")
  echo "PROFILE sweep size=$SIZE fov=$(python3 -c "print(f'{$FOV:.3f}')") src_tiles=$NT render=${RT}s hipsgen=${HT_}s end2end=${TOT}s"
  trash "$WK" 2>/dev/null || rm -rf "$WK"   # 省盘，只留 PROFILE 数字
done
echo "SWEEP_DONE"
