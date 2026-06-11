"""并行处理全部 Flatiron 分片，build 覆盖整个广州 FOV 的 G<Gmax 深星表渲染缓存。

为什么要它：probing 的小天区 NPZ 只覆盖两个窗口的 HEALPix 分片，渲出来是孤立
斑块、中间是数据空洞，看不出大裂隙的连续形态。要看真实银河形态必须用覆盖整个
FOV 的连续深星表。

工程（吸收 Fable 工程 advisory）：
  - 16 worker 并行（实测 16 最优，32 过订阅反而慢；瓶颈是单片 gzip 解压 ~0.7s）。
  - 每 worker：gzcat 解压到内存 → 跳 ECSV 注释头 → pyarrow.csv 读 4 列 →
    filter G<Gmax + FOV inside → 写 per-shard npy（不走 IPC 传大数组）。
  - 主进程拼所有 per-shard npy → 单个 NPZ，float32。

用法：
  python src/build_fov_deep_cache.py --gmax 20 --out data/raw/fov_g20.npz --workers 16
"""
import argparse
import glob
import io
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD_DIR = os.path.join(ROOT, "data", "raw", "flatiron_gaia_source_fov_gz")
sys.path.insert(0, os.path.join(ROOT, "src"))

# 相机参数（与 summary.txt / render_bortle_eye_grid 默认一致）
LAT_DEG = 23.13
LST_HOURS = 17.76
H_FOV = 90.0
V_REF = 75.0
PANEL_W, PANEL_H = 1080, 1920

USECOLS = ["l", "b", "phot_g_mean_mag", "bp_rp"]


def _fov_inside(l, b):
    """星是否落在广州 FOV 画面内（与 render_fov 共用同一取景几何）。"""
    from render_bortle_eye_grid import project_guangzhou_fov
    _, _, inside = project_guangzhou_fov(
        l, b, LAT_DEG, LST_HOURS, PANEL_W, PANEL_H, H_FOV, V_REF, "horizontal")
    return inside


def _ecsv_body_offset(raw):
    """ECSV 字节流里第一条非 # 行（CSV header）的起始字节偏移。"""
    pos = 0
    while pos < len(raw):
        nl = raw.find(b"\n", pos)
        if nl == -1:
            return len(raw)
        if raw[pos:pos + 1] != b"#":
            return pos
        pos = nl + 1
    return len(raw)


def process_shard(args):
    """worker：解压一个分片，filter G<gmax + FOV，写 per-shard npy，返回路径+计数。"""
    name, gmax, tmpdir = args
    import pyarrow as pa
    import pyarrow.csv as pacsv

    path = os.path.join(SHARD_DIR, name)
    raw = subprocess.run(["gzcat", path], capture_output=True).stdout
    off = _ecsv_body_offset(raw)
    body = raw[off:]
    try:
        tbl = pacsv.read_csv(
            io.BytesIO(body),
            read_options=pacsv.ReadOptions(use_threads=False),
            convert_options=pacsv.ConvertOptions(include_columns=USECOLS),
        )
    except Exception as e:
        return (None, 0, f"{name}: parse fail {e}")
    l = np.asarray(tbl["l"], dtype=np.float64)
    b = np.asarray(tbl["b"], dtype=np.float64)
    g = np.asarray(tbl["phot_g_mean_mag"], dtype=np.float64)
    bp_rp = np.asarray(tbl["bp_rp"], dtype=np.float64)
    ok = np.isfinite(l) & np.isfinite(b) & np.isfinite(g)
    l, b, g, bp_rp = l[ok], b[ok], g[ok], bp_rp[ok]
    keep = g < gmax
    l, b, g, bp_rp = l[keep], b[keep], g[keep], bp_rp[keep]
    if l.size:
        inside = _fov_inside(l, b)
        l, b, g, bp_rp = l[inside], b[inside], g[inside], bp_rp[inside]
    out = os.path.join(tmpdir, name.replace(".csv.gz", ".npy"))
    arr = np.stack([l, b, g, np.nan_to_num(bp_rp, nan=0.7)], axis=0).astype(np.float32)
    np.save(out, arr)
    return (out, int(l.size), None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gmax", type=float, default=20.0)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 片（调试用）。")
    args = p.parse_args()

    shards = sorted(os.path.basename(x) for x in
                    glob.glob(os.path.join(SHARD_DIR, "GaiaSource_*.csv.gz")))
    if args.limit:
        shards = shards[:args.limit]
    print(f"分片 {len(shards)}，G<{args.gmax}，{args.workers} worker")

    tmpdir = tempfile.mkdtemp(prefix="fov_deep_", dir=os.path.join(ROOT, "data", "raw"))
    os.environ["POLARS_MAX_THREADS"] = "1"
    tasks = [(s, args.gmax, tmpdir) for s in shards]
    parts, total, done, fails = [], 0, 0, []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(process_shard, t) for t in tasks]):
            out, n, err = fut.result()
            done += 1
            if err:
                fails.append(err)
            else:
                parts.append(out); total += n
            if done % 100 == 0 or done == len(shards):
                print(f"  {done}/{len(shards)} 片，累计 {total} 星", flush=True)
    if fails:
        print(f"  {len(fails)} 片解析失败: {fails[:3]}")

    print("合并 per-shard npy ...")
    cols = [np.load(p) for p in parts]
    big = np.concatenate(cols, axis=1)
    l, b, g, bp_rp = big[0], big[1], big[2], big[3]
    np.savez(args.out, l=l, b=b, g=g, bp_rp=bp_rp)
    print(f"保存 {args.out}: {l.size} 星 (G<{args.gmax}, FOV inside)")
    print(f"  G min/max: {g.min():.2f}/{g.max():.2f}")
    # 清理 per-shard 临时文件
    for pth in parts:
        os.remove(pth)
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main()
