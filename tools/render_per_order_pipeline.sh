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
JAVA="${JAVA:-java}"   # 远端机器 java 在 PATH；本机可 JAVA=/opt/homebrew/opt/openjdk@11/bin/java 覆盖
# 优先用 v12（minOrder=order 单层修好了，v11 是 bug）；没有则回退 v11。可 JAR=... 覆盖。
JAR="${JAR:-}"
if [ -z "$JAR" ]; then
  if [ -f outputs/tmp_reference_hips/AladinBeta_v12.jar ]; then JAR=outputs/tmp_reference_hips/AladinBeta_v12.jar
  else JAR=outputs/tmp_reference_hips/AladinBeta.jar; fi
fi

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
# hipsgen JVM 堆默认取「机器内存的一半，封顶 256g」——N8 per-order 有几万源 tile，hipsgen 要
# 缓存它们，8g（旧写死值）会 GC 抖死（实测 N8 6.6万源 tile，8g 32min 才 2.7%；256g 顺畅 600 tile/s）。
_memg=$(python3 -c "import os;print(int(os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/1024**3))" 2>/dev/null || echo 16)
XMX="${XMX:-$(python3 -c "print(min(max($_memg//2,8),256))")g}"
W=8; HPAR=3; HTH=8; DO_RENDER=1; DO_HIPSGEN=1; RESUME="--resume"; DARK_PSF="0.6"; STEPFRAC=0.8; BASE_TSIZE=2048
while [ $# -gt 0 ]; do
  case "$1" in
    --workers) W="$2"; shift 2;;
    --tile-size) BASE_TSIZE="$2"; shift 2;;   # TAN 方图边长（默认 2048，sweep 实测 N8 端到端最优）
    --hipsgen-par) HPAR="$2"; shift 2;;
    --hipsgen-th) HTH="$2"; shift 2;;
    --xmx) XMX="$2"; shift 2;;              # hipsgen JVM 堆（如 256g）；默认机器内存一半封顶 256g
    --tiles-only) DO_HIPSGEN=0; shift;;
    --hipsgen-only) DO_RENDER=0; shift;;
    --no-resume) RESUME=""; shift;;
    --dark-psf) DARK_PSF="$2"; shift 2;;   # 覆盖所有 order 的暗星 PSF（默认低层1.0/N8 0.6）
    --step-frac) STEPFRAC="$2"; shift 2;;  # TAN 方图步长=fov×此值（默认0.8重叠20%；1.0无重叠省~28% hipsgen 但可能有缝）
    *) echo "未知参数: $1"; exit 1;;
  esac
done

OUT="outputs/$TAG"; HIPS="$OUT/hips"; mkdir -p "$OUT"

# —— benchmark 表头：机器信息 + 配置，进 stdout（log file）——
# 跨机器公平对比用（M3 Ultra vs EPYC 等）。PROFILE 行机器可解析、人可读。tqdm 进度条走
# stderr（不进 log），profile/阶段标记走 stdout（tee 进 log）——log file 干净。
_ncpu=$(python3 -c "import os;print(os.cpu_count())" 2>/dev/null || echo "?")
_uname=$(uname -sm)
echo "PROFILE machine cpu=$_ncpu uname=\"$_uname\" workers=$W hipsgen_par=$HPAR hipsgen_th=$HTH xmx=$XMX step_frac=$STEPFRAC dark_psf=$DARK_PSF jar=$(basename $JAR)"
echo "PROFILE config tag=$TAG range=$LLO,$LHI/$BLO,$BHI orders=\"$ORDERS\""
_t_pipeline_start=$(python3 -c "import time;print(time.time())")
_now() { python3 -c "import time;print(time.time())"; }
_dur() { python3 -c "print(f'{$2-$1:.1f}')"; }   # _dur start end -> 秒

# 各 order 的渲染参数（cell/分辨率推导）
order_params() {  # $1=order -> echo "TFOV TILE_SIZE TSTEP PSF"
  python3 - "$1" "$HALF" "$DARK_PSF" "$STEPFRAC" "$BASE_TSIZE" <<'PY'
import sys
k=int(sys.argv[1]); half=float(sys.argv[2])
override=sys.argv[3] if len(sys.argv)>3 else ""
stepfrac=float(sys.argv[4]) if len(sys.argv)>4 and sys.argv[4] else 0.8
tsize=int(sys.argv[5]) if len(sys.argv)>5 and sys.argv[5] else 2048
cell=58.6323/2**k; cdelt=cell/512.0       # 该层瓦片像素分辨率
# 方图尺寸策略（不能一刀切 size=2048——低 order cell 大，size2048→fov 爆炸：N3 cell7.3°→
# fov29° gnomonic 严重畸变）。sweep 只在 N8 验证 size=2048 最优（fov0.92°、源 tile 少 hipsgen
# 快）。所以：
#   - 高 order（k>=6，源 tile 多、hipsgen 瓶颈层、cell 小→2048 的 fov 安全 ≤3.7°）：用 tsize。
#   - 低 order（k<6，cell 大、源 tile 本就少 hipsgen 不慢）：维持 fov=1.5×cell（size 恒 768），
#     fov 随 cell 大但 tile 数少、网格粗，是该这样；2048 会让 fov 到 7-29° 畸变。
if k >= 6:
    size = tsize; fov = size * cdelt
else:
    fov = 1.5 * cell; size = int(round(fov / cdelt))   # =768
# 天区比单方图还小时（小测试），方图别超过天区+余量，免得渲一堆空格
if half > 0 and fov > 2*half + 1.0:
    fov = 2*half + 1.0; size = max(int(round(fov/cdelt)), 256)
step = fov * stepfrac
# 默认：N8 锐点 0.6、低层 1.0 织底。--dark-psf 覆盖所有层（实测 0.6 各层更锐且不丢底）。
psf = float(override) if override else (0.6 if k>=8 else 1.0)
print(f"{fov:.4f} {size} {step:.4f} {psf}")
PY
}

# ---- 阶段1：渲 tiles（每 order 标定 + 渲，--resume 断点续传）----
if [ "$DO_RENDER" = 1 ]; then
  _t_render_start=$(_now)
  for k in $ORDERS; do
    read TFOV TILE_SIZE TSTEP PSF < <(order_params "$k")
    WK="$OUT/o$k"; mkdir -p "$WK/tiles"
    echo "=== [render] Norder$k: cdelt=$(python3 -c "print(f'{$TFOV*3600/$TILE_SIZE:.2f}')")\"/px fov=$TFOV size=$TILE_SIZE psf=$PSF W=$W ==="
    # 标定的 stdout 收进 stderr（不污染 benchmark log）
    python src/calibrate_alltile_tone.py --data "$NPZ" \
      --tile-fov "$TFOV" --tile-size "$TILE_SIZE" --value 6 --target-sky 0.020 \
      --star-contrast 4 --target-white 2.6 --psf-core-px "$PSF" --out "$WK/calib.json" >&2
    _ts=$(_now)
    # --progress：tqdm 走 stderr（终端可见、不进 log）。渲染器的 stdout（"瓦片完成"行）丢 stderr，
    # 真正的 PROFILE 行由本脚本统一产出，保证 log 格式一致、可解析。
    python src/render_tan_wcs.py --data "$NPZ" --out "$WK/tiles" --tiles \
      --l-range="$LLO,$LHI" --b-range="$BLO,$BHI" \
      --tile-fov "$TFOV" --tile-step "$TSTEP" --tile-size "$TILE_SIZE" --psf-core-px "$PSF" \
      --workers "$W" --value 6 --calib "$WK/calib.json" --progress $RESUME >&2
    _te=$(_now); _el=$(_dur $_ts $_te)
    _ntiles=$(find "$WK/tiles" -name "*.png" 2>/dev/null | wc -l | tr -d ' ')
    _tp=$(python3 -c "print(f'{$_ntiles/max($_el,0.001):.2f}')")
    echo "PROFILE render N$k tiles=$_ntiles elapsed=${_el}s throughput=${_tp}tile/s"
  done
  echo "PROFILE render_total elapsed=$(_dur $_t_render_start $(_now))s"
  echo "TILES_DONE [$TAG]"
fi

# ---- 阶段2：hipsgen 各 order（可并行）+ 组装金字塔 ----
if [ "$DO_HIPSGEN" = 1 ]; then
  _t_hipsgen_start=$(_now)
  hipsgen_one() {
    local k="$1"
    [ -d "$OUT/o$k/tiles" ] || { echo "  ⚠ o$k/tiles 不存在，跳过" >&2; return; }
    trash "$OUT/o$k/hips" 2>/dev/null || rm -rf "$OUT/o$k/hips" 2>/dev/null || true
    local s=$(_now)
    # minOrder=order：只建该层、不池化下树（v12 修好；v11 仍出全树但下层不耗时）。
    # hipsgen 全 log 进各自文件（不进主 stdout，保 benchmark log 干净）。
    "$JAVA" -Xmx"$XMX" -jar "$JAR" -hipsgen in="$OUT/o$k/tiles" out="$OUT/o$k/hips" \
      color=jpeg fading=true minOrder="$k" order="$k" maxThread="$HTH" creator_did=DuckBro obs_title="$TAG" \
      "target=$LC $BC" INDEX TILES > "$OUT/o$k.hipsgen.log" 2>&1
    local e=$(_now)
    local cells=$(find "$OUT/o$k/hips/Norder$k" -name "*.jpg" 2>/dev/null | wc -l | tr -d ' ')
    echo "PROFILE hipsgen N$k cells=$cells elapsed=$(_dur $s $e)s"
  }
  echo "=== [hipsgen] minOrder=order 单层，并行 $HPAR×$HTH 线程 ==="
  running=0
  for k in $ORDERS; do
    hipsgen_one "$k" &
    running=$((running+1))
    [ "$running" -ge "$HPAR" ] && { wait -n 2>/dev/null || wait; running=$((running-1)); }
  done
  wait
  echo "PROFILE hipsgen_total elapsed=$(_dur $_t_hipsgen_start $(_now))s"

  echo "=== 组装金字塔：各 order 取自己的 NorderK ==="
  trash "$HIPS" 2>/dev/null || true; mkdir -p "$HIPS"
  for k in $ORDERS; do
    [ -d "$OUT/o$k/hips/Norder$k" ] && cp -R "$OUT/o$k/hips/Norder$k" "$HIPS/Norder$k" && echo "  Norder$k ← o$k"
  done
  HI=$(echo $ORDERS | tr ' ' '\n' | sort -n | tail -1)
  LO=$(echo $ORDERS | tr ' ' '\n' | sort -n | head -1)
  cp "$OUT/o$HI/hips/properties" "$HIPS/properties" 2>/dev/null || true
  cp "$OUT/o$HI/hips/Moc.fits" "$HIPS/Moc.fits" 2>/dev/null || true
  # 修 minOrder=order 单层 hipsgen 的副作用：每个 order 的 properties 写 hips_order_min=该order，
  # 拷最高 order 的会得 min=HI，导致 Aladin 以为没有低 order、zoom-out 一片空白。组装后的金字塔
  # 实含 LO..HI 全层，必须把 order_min 改回最低 order（没有则补一行）。
  if grep -q "^hips_order_min" "$HIPS/properties" 2>/dev/null; then
    sed -i.bak "s/^hips_order_min.*/hips_order_min       = $LO/" "$HIPS/properties" && rm -f "$HIPS/properties.bak"
  else
    echo "hips_order_min       = $LO" >> "$HIPS/properties"
  fi
  python src/rebuild_allsky_hires.py --hips "$HIPS" --order "$LO" >&2 || true
  cp skills/hips_landing_page.html "$HIPS/index.html" 2>/dev/null || true
  echo "PER_ORDER_DONE → $HIPS （orders: $ORDERS）"
fi
echo "PROFILE pipeline_total elapsed=$(_dur $_t_pipeline_start $(_now))s"
