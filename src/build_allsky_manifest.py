"""生成全天 Flatiron GaiaSource 下载清单（manifest + urls），并报告相对已有 FOV 子集的
真实增量字节数。

与 docs/gaia_catalog_usage.md 的 FOV 清单生成同源，但**去掉 FOV 过滤**：解析 Flatiron
目录页的全部 GaiaSource_*.csv.gz（每个文件自带字节数），即全天清单。Flatiron 文件按
HEALPix range 切、不按星等，文件字节 ∝ 该天区星密度——所以增量不能用面积比外推，必须
逐文件求和（银心 FOV 那 21% 面积因含银心装了 412G，剩余天区稀疏得多）。

用法：
  python src/build_allsky_manifest.py --out data/raw/flatiron_allsky \
      --have data/raw/flatiron_gaia_source_fov_gz   # 已有子集，增量清单跳过这些
  # 只看体量不写文件：--dry-run
"""
import argparse
import csv
import os
import re
import urllib.request

BASE = "https://sdsc-users.flatironinstitute.org/~gaia/dr3/csv/GaiaSource/"


def fetch_file_list():
    html = urllib.request.urlopen(BASE, timeout=120).read().decode("utf-8", "replace")
    pat = re.compile(
        r'data-order="(GaiaSource_(\d{6})-(\d{6})\.csv\.gz)".*?<td data-order="(\d+)">', re.S)
    files = []
    for m in pat.finditer(html):
        files.append((int(m.group(2)), int(m.group(3)), int(m.group(4)),
                      m.group(1), BASE + m.group(1)))
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="输出目录（写 manifest.csv / urls.txt / urls_delta.txt）")
    ap.add_argument("--have", default=None,
                    help="已有子集目录（其 manifest.csv 里的文件从增量清单剔除）")
    ap.add_argument("--dry-run", action="store_true", help="只打印体量，不写文件")
    args = ap.parse_args()

    files = fetch_file_list()
    total = sum(f[2] for f in files)
    print(f"Flatiron 全集：{len(files)} 文件，{total/1024**3:.1f} GiB", flush=True)

    have_names = set()
    if args.have:
        man = os.path.join(args.have, "manifest.csv")
        if os.path.isfile(man):
            with open(man) as f:
                have_names = {r["name"] for r in csv.DictReader(f)}
    delta = [f for f in files if f[3] not in have_names]
    delta_bytes = sum(f[2] for f in delta)
    print(f"已有 {len(have_names)} 文件；增量 {len(delta)} 文件，{delta_bytes/1024**3:.1f} GiB 待下载",
          flush=True)

    if args.dry_run or not args.out:
        return
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "manifest.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "url", "healpix8_min", "healpix8_max", "size_bytes"])
        for lo, hi, sz, name, url in files:
            w.writerow([name, url, lo, hi, sz])
    with open(os.path.join(args.out, "urls.txt"), "w") as f:
        f.write("\n".join(x[4] for x in files) + "\n")
    with open(os.path.join(args.out, "urls_delta.txt"), "w") as f:
        f.write("\n".join(x[4] for x in delta) + "\n")
    print(f"写出：manifest.csv（全天 {len(files)}）、urls.txt（全天）、"
          f"urls_delta.txt（增量 {len(delta)}）→ {args.out}", flush=True)


if __name__ == "__main__":
    main()
