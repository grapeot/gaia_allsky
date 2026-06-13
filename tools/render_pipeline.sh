#!/bin/bash
# Gaia HiPS 渲染 pipeline：标定 → 渲 tile（分桶 memory-aware）→[可选] hipsgen + Allsky + 落地页。
# output 按 tag 隔离到 outputs/<tag>/（calib.json / tiles/ / hips/），不同区域互不干扰。
#
# 数据用分桶 npz（build_healpix_bucketed.py 产出，含 bucket_start）——每 tile 只读邻桶，
# 内存随 worker 线性、单 tile 几十 MB，可上几十~上百进程。亮星 bloom 已按角尺寸自动放大。
#
# 用法：
#   bash tools/render_pipeline.sh <tag> <hpx_npz> <l-range> <b-range> [tfov] [tstep] [tsize] [workers] [--hipsgen]
# 例（广州 FOV 高分 1.5arcsec/px，96 进程，本机不拼 hipsgen）：
#   bash tools/render_pipeline.sh gz data/raw/fov_g20_bsc5_hpx6.npz -41,79 -31,43 0.64 0.533 1536 96
# 例（全天高分 + 本机 hipsgen）：
#   bash tools/render_pipeline.sh allsky data/raw/gaia_allsky_g20_bsc5_hpx6.npz 0,360 -90,90 0.64 0.533 1536 96 --hipsgen
#
# 环境变量（hipsgen 用）：JAVA=java XMX=400g MAXTHREAD=96 HIPS_ORDER=8
set -e
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate

TAG="${1:?需要 tag（output 目录名，如 gz / allsky）}"
HPX="${2:?需要分桶 npz 路径（build_healpix_bucketed 产出，含 bucket_start）}"
LRANGE="${3:?需要 l-range，如 -41,79 或 0,360}"
BRANGE="${4:?需要 b-range，如 -31,43 或 -90,90}"
TFOV="${5:-0.64}"; TSTEP="${6:-0.533}"; TSIZE="${7:-1536}"; W="${8:-24}"
DO_HIPSGEN=0; [ "${9}" = "--hipsgen" ] && DO_HIPSGEN=1

OUT_DIR="outputs/$TAG"
CALIB="$OUT_DIR/calib.json"; TILES="$OUT_DIR/tiles"; HIPS="$OUT_DIR/hips"
mkdir -p "$OUT_DIR"

echo "=== [1/2] 全天 tone 标定（hero 同款 sc=4；fov/size 须与渲染一致）==="
python src/calibrate_alltile_tone.py --data "$HPX" \
  --tile-fov "$TFOV" --tile-size "$TSIZE" --value 6 --target-sky 0.020 \
  --star-contrast 4 --target-white 2.6 --out "$CALIB"

echo "=== [2/2] 渲 tile（分桶 memory-aware，$W 进程，1.5 arcsec/px，6× bloom）==="
[ -e "$TILES" ] && trash "$TILES" 2>/dev/null || rm -rf "$TILES" 2>/dev/null || true
mkdir -p "$TILES"
python src/render_tan_wcs.py --data "$HPX" --out "$TILES" --tiles \
  --l-range="$LRANGE" --b-range="$BRANGE" \
  --tile-fov "$TFOV" --tile-step "$TSTEP" --tile-size "$TSIZE" --workers "$W" \
  --value 6 --calib "$CALIB"
echo "TILES_DONE → $TILES"

if [ "$DO_HIPSGEN" = "1" ]; then
  JAVA="${JAVA:-java}"; XMX="${XMX:-400g}"; MAXTHREAD="${MAXTHREAD:-$W}"; HIPS_ORDER="${HIPS_ORDER:-8}"
  echo "=== [+] hipsgen（hips_order=$HIPS_ORDER）+ Allsky + 落地页 ==="
  [ -e "$HIPS" ] && (trash "$HIPS" 2>/dev/null || rm -rf "$HIPS")
  "$JAVA" -Xmx"$XMX" -jar outputs/tmp_reference_hips/AladinBeta.jar -hipsgen \
    in="$TILES" out="$HIPS" color=jpeg hips_order="$HIPS_ORDER" maxthread="$MAXTHREAD" \
    creator_did=DuckBro obs_title=GaiaMW1B "target=271.672 -25.873" fading=true
  python src/rebuild_allsky_hires.py --hips "$HIPS"
  cp skills/hips_landing_page.html "$HIPS/index.html"
  echo "HIPS_DONE → $HIPS"
else
  echo "（跳过 hipsgen——大规模时 rsync $TILES 到更强机器单独拼，见 skill 3.4）"
fi
echo "PIPELINE_DONE [$TAG]"
