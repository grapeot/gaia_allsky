"""获取二维全天渲染所需的 Gaia DR3 全天缓存。

渲染器需要一个包含银道经纬度、Gaia G 星等和 BP-RP 颜色的 NPZ。
Gaia 数据是公开科学数据，但生成的缓存属于较大的本地文件，不应进入 Git。
"""
import argparse
import os

import numpy as np


OUT = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "gaia_g11.npz")


def build_query(gmax=11.0, row_limit=None, gmin=None):
    """Gaia 档案库对匿名查询有 300 万行上限，超限会被无声截断。

    取暗星段时必须用 gmin/gmax 分段（每段估算 <300 万行）再拼接。
    """
    limit = f"TOP {int(row_limit)} " if row_limit is not None else ""
    gmin_clause = f"\n      AND phot_g_mean_mag >= {float(gmin)}" if gmin is not None else ""
    return f"""
    SELECT {limit}l, b, phot_g_mean_mag, bp_rp
    FROM gaiadr3.gaia_source
    WHERE phot_g_mean_mag < {float(gmax)}{gmin_clause}
      AND l IS NOT NULL
      AND b IS NOT NULL
      AND phot_g_mean_mag IS NOT NULL
    """.strip()


def table_to_arrays(table):
    l = np.asarray(table["l"], float)
    b = np.asarray(table["b"], float)
    g = np.asarray(table["phot_g_mean_mag"], float)
    bp_rp = np.nan_to_num(np.asarray(table["bp_rp"], float), nan=0.7)
    return l, b, g, bp_rp


def fetch(gmax=11.0, out=None, row_limit=None, gmin=None):
    """获取 Gaia DR3 全天恒星，并把 l/b/g/bp_rp 数组保存为 NPZ。"""
    from astroquery.gaia import Gaia

    query = build_query(gmax, row_limit, gmin)
    rng = f"{gmin}<=G<{gmax}" if gmin is not None else f"G<{gmax}"
    print(f"查询 Gaia DR3 全天 {rng} 恒星...")
    job = Gaia.launch_job_async(query)
    table = job.get_results()
    print(f"得到 {len(table)} 颗星")
    l, b, g, bp_rp = table_to_arrays(table)
    out = out or OUT
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, l=l, b=b, g=g, bp_rp=bp_rp)
    print(f"已保存 {out}: {len(l)} 颗星，G 星等范围 {g.min():.2f}-{g.max():.2f}")
    return l, b, g, bp_rp


def build_parser():
    parser = argparse.ArgumentParser(description="获取 gaia_allsky 渲染所需的 Gaia DR3 全天缓存。")
    parser.add_argument("--gmax", type=float, default=11.0, help="Gaia G 星等上限。")
    parser.add_argument("--gmin", type=float, default=None, help="Gaia G 星等下限（分段取数用，匿名查询单段需 <300 万行）。")
    parser.add_argument("--output", default=OUT, help="输出 NPZ 路径。")
    parser.add_argument("--row-limit", type=int, default=None, help="可选 TOP N 限制，用于冒烟测试。")
    parser.add_argument("--dry-run", action="store_true", help="只打印 ADQL 查询，不实际执行。")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.dry_run:
        print(build_query(args.gmax, args.row_limit, args.gmin))
        return None
    return fetch(args.gmax, args.output, args.row_limit, args.gmin)


if __name__ == "__main__":
    main()
