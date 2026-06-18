#!/bin/bash
# 十亿像素 HiPS 全流程一条龙：全天 tone 标定 → 渲 tile（用 calib）→ PixInsight 批处理 →
# hipsgen 拼金字塔 → rebuild 高分 Allsky → 覆盖样式化落地页。
#
# 设计要点（见 skills/hips_1b_tile_generation.md）：
# - 先标定再渲染：全天固定 (sky_anchor, star_contrast, stretch) 复刻 hero 对比且块间无接缝。
# - 标定必须用与渲染相同的 tile-fov/tile-size（sky_anchor 依赖立体角归一化 norm）。
# - PixInsight 批处理用 pixinsight_batch.py，已内置 shm 段防泄漏（避免「卡死一半 worker」）。
#
# 用法：
#   bash tools/build_hips_pipeline.sh <tag> <tile_fov> <tile_step> <tile_size> [workers]
# 例（4K 快验证）:  bash tools/build_hips_pipeline.sh 4khero 20 16 650 8
# 例（十亿像素正式）: bash tools/build_hips_pipeline.sh 1b   6  5  2048 8
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

TAG="${1:?需要 tag，如 1b / 4khero}"
TFOV="${2:?tile-fov}"; TSTEP="${3:?tile-step}"; TSIZE="${4:?tile-size}"; W="${5:-8}"
DATA=data/raw/fov_g20_bsc5.npz
LRANGE="-41,79"; BRANGE="-31,43"
TILES="outputs/hips1b_tiles_${TAG}"
OUT="outputs/hips1b_out_${TAG}"
CALIB="outputs/alltile_calib_${TAG}.json"

echo "=== [0/5] 全天 tone 标定 (fov=$TFOV size=$TSIZE, hero 同款 sc=4) ==="
python src/calibrate_alltile_tone.py --data "$DATA" \
  --tile-fov "$TFOV" --tile-size "$TSIZE" --value 6 --target-sky 0.020 \
  --star-contrast 4 --target-white 2.6 --out "$CALIB"

echo "=== [1/5] 渲 tile (用 calib) ==="
[ -e "$TILES" ] && trash "$TILES"; mkdir -p "$TILES"
python src/render_tan_wcs.py --data "$DATA" --out "$TILES" --tiles \
  --l-range="$LRANGE" --b-range="$BRANGE" \
  --tile-fov "$TFOV" --tile-step "$TSTEP" --tile-size "$TSIZE" --workers "$W" \
  --value 6 --calib "$CALIB"

echo "=== [2/5] PixInsight 批处理（色温/去绿精修；shm 防泄漏已内置）==="
python tools/pixinsight_batch.py --xpsm skills/batch_process_frames.xpsm \
  --in "$TILES" --in-place --workers "$W" --slot-base 200

echo "=== [3/5] hipsgen 拼金字塔 ==="
# hips_order=8：显式限最深 Norder=8（≈1.6 arcsec/px，匹配 1.5arcsec/px 源真分辨率）。
# 不限的话 hipsgen 按源像素密度自动选 Norder9（0.8arcsec，对源 2× 过采样插值、无新信息），
# 多拼一整层 → 瓦片 4×（56万 vs 14万）、时间 12h vs 3.5h。Norder8 画质不损、省 75%。
# 低分辨率源（如 1B 的 10arcsec）应相应调小，或去掉让其自适应。
[ -e "$OUT" ] && trash "$OUT" || true
/opt/homebrew/opt/openjdk@11/bin/java -Xmx80g \
  -jar outputs/tmp_reference_hips/AladinBeta.jar -hipsgen \
  in="$TILES" out="$OUT" color=jpeg hips_order=8 maxthread=32 \
  creator_did=DuckBro obs_title=GaiaMW1B "target=271.672 -25.873" fading=true

echo "=== [4/5] rebuild 高分 Allsky（修 zoom-out 糊）==="
python src/rebuild_allsky_hires.py --hips "$OUT"

echo "=== [5/5] 覆盖样式化落地页 ==="
cp skills/hips_landing_page.html "$OUT/index.html"
cp skills/hips_landing_page-en.html "$OUT/index-en.html" 2>/dev/null || true

echo "PIPELINE_DONE → $OUT"
