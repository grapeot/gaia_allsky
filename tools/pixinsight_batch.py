#!/usr/bin/env python3
"""并行用 PixInsight 命令行批处理一批图：把一个 .xpsm process icon 应用到目录里所有图。

设计（为什么这样）：PixInsight 单实例难并行，但用多 instance（`-n=slot`，各占一个
application slot）+ Python 调度就能并行。本脚本解析 xpsm 拿到 process 链，把文件列表分
N 份，每份动态生成一个 worker JS，并行起 N 个 headless PixInsight instance 各处理一份，
全跑完 join。

关键坑（实测）：
- **不指定 slot 时新调用会 yield 给已运行实例、worker JS 不执行**。解法不是 pkill（那会
  杀掉 GUI、阻止并存），而是用 `-n=<高 slot>`（默认从 200 起）显式起独立实例——实测高
  slot 实例与 GUI/其它任务**完美并存、互不 yield**。多个 batch 并行给不同 `--slot-base`。
- worker JS **不要带 `--force-exit`**（它会在脚本执行前就退出）；脚本末尾自己
  `Console.terminate()` 退出。
- worker JS 第一行就写日志文件确认执行；executeOn(view, false) 的 false=no swap file（省时）。
- xpsm 有命名空间 `xmlns=http://www.pixinsight.com/xpsm`，解析要带 ns。
- `enabled="false"` 的 process 是 GUI 里禁用的，跳过。

用法：
  python tools/pixinsight_batch.py --xpsm <icon.xpsm> --in <dir> --out <dir> --workers 10
  # in 目录里所有 *.png 被处理，写到 out 目录（同名）。--in-place 则覆盖原文件。
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

NS = "{http://www.pixinsight.com/xpsm}"
PI = "/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight"


def parse_xpsm(path):
    """解析 xpsm 的 ProcessContainer，返回启用的 process 列表（dict）。
    只支持本项目用到的 CurvesTransformation + SCNR；其它类型报错提示扩展。"""
    root = ET.parse(path).getroot()
    container = root.find(f"{NS}instance")
    procs = []
    for inst in container.findall(f"{NS}instance"):
        cls = inst.get("class")
        if inst.get("enabled", "true") == "false":
            continue  # GUI 里禁用的跳过
        if cls == "CurvesTransformation":
            chans = {}
            for tbl in inst.findall(f"{NS}table"):
                cid = tbl.get("id")
                pts = []
                for tr in tbl.findall(f"{NS}tr"):
                    tds = {td.get("id"): td.get("value") for td in tr.findall(f"{NS}td")}
                    pts.append([float(tds["x"]), float(tds["y"])])
                if pts != [[0.0, 0.0], [1.0, 1.0]]:  # 非默认直线才保留
                    chans[cid] = pts
            procs.append({"cls": "CurvesTransformation", "chans": chans})
        elif cls == "SCNR":
            params = {p.get("id"): p.get("value") for p in inst.findall(f"{NS}parameter")}
            procs.append({"cls": "SCNR", "params": params})
        else:
            sys.exit(f"未支持的 process 类型 {cls}，请在 pixinsight_batch.py 里加重建逻辑")
    return procs


def procs_to_js(procs):
    """把解析出的 process 链转成 JS 重建 + executeOn 的代码片段。"""
    lines = []
    for i, p in enumerate(procs):
        v = f"P{i}"
        if p["cls"] == "CurvesTransformation":
            lines.append(f"var {v}=new CurvesTransformation;")
            for cid, pts in p["chans"].items():
                arr = "[" + ",".join(f"[{x},{y}]" for x, y in pts) + "]"
                lines.append(f"{v}.{cid}={arr};")
        elif p["cls"] == "SCNR":
            lines.append(f"var {v}=new SCNR;")
            pm = p["params"]
            if "amount" in pm:
                lines.append(f"{v}.amount={pm['amount']};")
            if "protectionMethod" in pm:
                lines.append(f"{v}.protectionMethod=SCNR.prototype.{pm['protectionMethod']};")
            if "colorToRemove" in pm:
                lines.append(f"{v}.colorToRemove=SCNR.prototype.{pm['colorToRemove']};")
            if "preserveLightness" in pm:
                lines.append(f"{v}.preserveLightness={pm['preserveLightness']};")
        lines.append(f"{v}.executeOn(w.mainView,false);")
    return "\n".join(lines)


def make_worker_js(js_path, files, out_dir, in_place, proc_js, done_path):
    """生成一个 worker JS：处理给定文件列表，每张 open→apply→saveAs。"""
    file_arr = ",".join(f'"{f}"' for f in files)
    out_expr = ("inp" if in_place
                else f'"{out_dir}/" + File.extractName(inp) + File.extractSuffix(inp)')
    js = f"""
var FILES=[{file_arr}];
var done=new File;done.createForWriting("{done_path}");
for (var i=0;i<FILES.length;i++){{
  var inp=FILES[i];
  try{{
    var w=ImageWindow.open(inp)[0];
    {proc_js}
    var outp={out_expr};
    w.saveAs(outp,false,false,false,false);
    w.forceClose();
    done.outTextLn("OK "+inp);done.flush();
  }}catch(e){{ done.outTextLn("ERR "+inp+" : "+e.toString());done.flush(); }}
}}
done.outTextLn("__WORKER_DONE__");done.close();
try{{Console.terminate();}}catch(e){{}}
"""
    with open(js_path, "w") as f:
        f.write(js)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xpsm", required=True)
    ap.add_argument("--in", dest="indir", required=True, help="输入图目录（处理所有 *.png）")
    ap.add_argument("--out", dest="outdir", default=None, help="输出目录（默认 --in-place）")
    ap.add_argument("--in-place", action="store_true", help="覆盖原文件")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--slot-base", type=int, default=200,
                    help="PixInsight application slot 基址（worker 用 slot_base..+N-1）。"
                         "用高 slot 避开 GUI/其它任务，从而不用 pkill、可并存。多个 batch "
                         "并行时给不同基址（如 200 / 220）避免撞 slot。范围 [1,256]。")
    ap.add_argument("--pattern", default="*.png")
    ap.add_argument("--timeout", type=int, default=1800, help="总超时秒")
    args = ap.parse_args()

    if not args.in_place and not args.outdir:
        sys.exit("需 --out 或 --in-place")
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(args.indir, args.pattern)))
    if not files:
        sys.exit(f"{args.indir} 里没有 {args.pattern}")
    procs = parse_xpsm(args.xpsm)
    print(f"解析 xpsm：{len(procs)} 个启用 process "
          f"({', '.join(p['cls'] for p in procs)})；{len(files)} 张图，{args.workers} worker", flush=True)
    proc_js = procs_to_js(procs)

    work = "/tmp/pi_batch_work"
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)

    # 分片
    n = min(args.workers, len(files))
    shards = [files[i::n] for i in range(n)]
    procs_running = []
    done_paths = []
    # 从高 slot 基址起（默认 200），避开 GUI 和其它任务占的低 slot——这样**不用 pkill**，
    # 能和你 GUI、其它 batch 任务并存（实测：高 slot 独立实例不 yield、互不干扰）。
    # 多个 batch 并行时给不同 --slot-base 即可。
    for i, shard in enumerate(shards):
        slot = args.slot_base + i
        js_path = os.path.join(work, f"worker_{i}.js")
        done_path = os.path.join(work, f"done_{i}.log")
        done_paths.append(done_path)
        make_worker_js(js_path, shard,
                       args.outdir if args.outdir else "", args.in_place, proc_js, done_path)
        p = subprocess.Popen(
            [PI, f"-n={slot}", "--automation-mode", "--no-splash", f"-r={js_path}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs_running.append(p)
    print(f"起了 {n} 个 PixInsight instance（slot {args.slot_base}..{args.slot_base+n-1}），"
          f"各处理 ~{len(shards[0])} 张（不 pkill，与 GUI/其它任务并存）", flush=True)

    # join：轮询 done 标记
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        finished = sum(1 for d in done_paths
                       if os.path.isfile(d) and "__WORKER_DONE__" in open(d).read())
        processed = sum(open(d).read().count("OK ") for d in done_paths if os.path.isfile(d))
        print(f"  {int(time.time()-t0)}s: {finished}/{n} worker 完成, {processed}/{len(files)} 张处理", flush=True)
        if finished == n:
            break
        time.sleep(10)

    # 收尾
    for p in procs_running:
        if p.poll() is None:
            p.terminate()
    ok = sum(open(d).read().count("OK ") for d in done_paths if os.path.isfile(d))
    err = sum(open(d).read().count("ERR ") for d in done_paths if os.path.isfile(d))
    print(f"DONE: {ok} 成功, {err} 失败 / {len(files)} 张", flush=True)
    if err:
        for d in done_paths:
            if os.path.isfile(d):
                for ln in open(d):
                    if ln.startswith("ERR"):
                        print("  " + ln.strip())


if __name__ == "__main__":
    main()
