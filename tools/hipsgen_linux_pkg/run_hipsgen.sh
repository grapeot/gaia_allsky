#!/bin/bash
# 在 Linux 上把渲染好的 TAN 瓦片（PNG + .hhh WCS）拼成 HiPS 金字塔。
# 只做 hipsgen + 高分 Allsky 重建 + 落地页——瓦片渲染/PixInsight 在别处（mac）做完后 rsync 过来。
#
# 用法：
#   TILES=/path/to/tiles OUT=/path/to/hips_out bash run_hipsgen.sh
# 可选环境变量：
#   JAVA=/path/to/openjdk11/bin/java   （默认找 PATH 里的 java，须是 JDK 11）
#   HIPS_ORDER=8                        （默认 8，见 README 的「为什么限 order」）
#   XMX=80g                             （JVM 堆，默认 80g，按机器内存调）
#   MAXTHREAD=32                        （并行线程，默认 32）
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

: "${TILES:?需要 TILES=瓦片目录（含 *.png + *.hhh）}"
: "${OUT:?需要 OUT=HiPS 输出目录}"
JAVA="${JAVA:-java}"
HIPS_ORDER="${HIPS_ORDER:-8}"
XMX="${XMX:-80g}"
MAXTHREAD="${MAXTHREAD:-32}"
JAR="$HERE/bin/AladinBeta.jar"

echo "=== 检查 Java（须 JDK 11，新版 JDK 不兼容此 jar）==="
"$JAVA" -version 2>&1 | head -1

echo "=== [1/3] hipsgen 拼金字塔（hips_order=$HIPS_ORDER）==="
# hips_order 显式限最深 Norder——不限的话 hipsgen 按源像素密度自动选更深一层做过采样插值
# （无新信息、糊、瓦片 4×、时间数倍）。order=8 ≈ 1.6 arcsec/px，匹配 1.5 arcsec/px 源真分辨率。
[ -e "$OUT" ] && rm -rf "$OUT"   # Linux 无 trash；如需保留旧产物先改名
"$JAVA" -Xmx"$XMX" -jar "$JAR" -hipsgen \
  in="$TILES" out="$OUT" color=jpeg hips_order="$HIPS_ORDER" maxthread="$MAXTHREAD" \
  creator_did=DuckBro obs_title=GaiaMW1B "target=271.672 -25.873" fading=true

echo "=== [2/3] 重建高分 Allsky（修 zoom-out 糊）==="
python3 "$HERE/src/rebuild_allsky_hires.py" --hips "$OUT"

echo "=== [3/3] 覆盖样式化落地页 ==="
cp "$HERE/src/hips_landing_page.html" "$OUT/index.html"

echo "HIPSGEN_DONE → $OUT"
echo "本地预览：cd $OUT && python3 -m http.server 8080  然后浏览器开 http://<本机>:8080/"
