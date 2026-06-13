#!/bin/bash
# 现打 Linux hipsgen 交接包：拷入 jar（不进 git）→ tar。产出 hipsgen_linux_pkg.tar.gz。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cp "$ROOT/outputs/tmp_reference_hips/AladinBeta.jar" "$HERE/bin/"
cd "$(dirname "$HERE")"
tar czf hipsgen_linux_pkg.tar.gz hipsgen_linux_pkg
echo "包好了：$(dirname "$HERE")/hipsgen_linux_pkg.tar.gz"
echo "rsync: rsync -avz $(dirname "$HERE")/hipsgen_linux_pkg.tar.gz user@linux:/data/"
