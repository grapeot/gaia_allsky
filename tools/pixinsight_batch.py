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
PI_SHM_KEY_PREFIX = "0x510f"  # PixInsight 的 SysV shm key 前缀（实测）


def _shm_count():
    """系统当前 SysV shm 段总数（macOS 全局上限 kern.sysv.shmmni 默认 32）。"""
    try:
        out = subprocess.run(["ipcs", "-m"], capture_output=True, text=True).stdout
        return sum(1 for ln in out.splitlines() if ln.startswith("m "))
    except Exception:
        return -1


def _shm_limit():
    try:
        out = subprocess.run(["sysctl", "-n", "kern.sysv.shmmni"],
                             capture_output=True, text=True).stdout.strip()
        return int(out)
    except Exception:
        return 32


def _cleanup_pi_shm():
    """清理无进程附着（NATTCH=0）的 PixInsight 残留 shm 段，防泄漏累积占满配额。
    每个 PI 实例占 1 个 shm 段；崩溃/被杀的实例会泄漏不释放，反复后占满 shmmni(32)，
    导致后续 PI 实例 QSharedMemory::create 失败、启动即崩——这是 batch 卡死的真根因。
    只删 PI key 前缀 + NATTCH=0 的段（无附着=无进程在用，删除安全）。"""
    try:
        out = subprocess.run(["ipcs", "-m", "-o"], capture_output=True, text=True).stdout
    except Exception:
        return 0
    removed = 0
    for ln in out.splitlines():
        f = ln.split()
        # 格式: m <id> <key> <mode> <owner> <group> <nattch>
        if len(f) >= 7 and f[0] == "m" and f[2].startswith(PI_SHM_KEY_PREFIX) and f[6] == "0":
            if subprocess.run(["ipcrm", "-m", f[1]], capture_output=True).returncode == 0:
                removed += 1
    return removed


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
    ap.add_argument("--resume", action="store_true",
                    help="断点续传：读输入目录的 .pi_batch_done.log，跳过已处理的文件只跑差集"
                         "（避免 in-place 重处理双重调色）。中断后重跑加这个。")
    ap.add_argument("--timeout", type=int, default=1800, help="总超时秒")
    ap.add_argument("--stagger", type=float, default=2.0,
                    help="实例间错开启动秒数，缓解瞬时资源争用。0=同时起。注意 batch 卡死的真"
                         "根因是 SysV shm 段耗尽（崩溃实例泄漏僵尸段占满 shmmni=32），已由跑前"
                         "清残留+余量降并发+收尾清理解决，stagger 只是辅助。")
    args = ap.parse_args()

    if not args.in_place and not args.outdir:
        sys.exit("需 --out 或 --in-place")
    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(args.indir, args.pattern)))
    if not files:
        sys.exit(f"{args.indir} 里没有 {args.pattern}")
    # 断点续传：持久 done log 累积所有处理过的文件（in-place 时尤其重要——重处理会双重调色）。
    # 工作目录 /tmp/pi_batch_work 每次清空，所以 done log 另存到输入目录旁。--resume 时读它
    # 剔除已处理的，只跑差集。注意：mtime 不可靠（渲染/PI 多进程乱序并发写、交织无分界，实测
    # 最大断层仅 10s），done log 才是精确来源。
    persist_done = os.path.join(args.indir, ".pi_batch_done.log")
    if args.resume and os.path.isfile(persist_done):
        processed_before = set()
        for ln in open(persist_done):
            if ln.startswith("OK "):
                processed_before.add(os.path.abspath(ln.split("OK ", 1)[1].strip()))
        before = len(files)
        files = [f for f in files if os.path.abspath(f) not in processed_before]
        print(f"--resume：done log 记录已处理 {len(processed_before)}，跳过；本次只处理 "
              f"{len(files)}/{before}", flush=True)
        if not files:
            print("全部已处理，无需续跑。", flush=True)
            return
    procs = parse_xpsm(args.xpsm)
    print(f"解析 xpsm：{len(procs)} 个启用 process "
          f"({', '.join(p['cls'] for p in procs)})；{len(files)} 张图，{args.workers} worker", flush=True)
    proc_js = procs_to_js(procs)

    work = "/tmp/pi_batch_work"
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)

    # 跑前清残留 shm + 检测余量。每个 PI 实例占 1 个 SysV shm 段，系统上限 shmmni（默认 32）。
    # 残留僵尸段（崩溃泄漏）会占满配额让新实例 QSharedMemory::create 失败。先清无附着的 PI 段。
    freed = _cleanup_pi_shm()
    if freed:
        print(f"清理了 {freed} 个无附着的 PixInsight 残留 shm 段", flush=True)
    shm_now, shm_max = _shm_count(), _shm_limit()
    n = min(args.workers, len(files))
    if shm_now >= 0 and shm_now + n > shm_max:
        safe = max(1, shm_max - shm_now - 1)
        print(f"⚠ shm 余量不足：已用 {shm_now}/{shm_max}，{n} worker 会触顶。降到 {safe} worker。"
              f"（每实例占 1 段；如需更高并发，sudo sysctl -w kern.sysv.shmmni=128）", flush=True)
        n = safe

    # 分片
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
        # 错开启动：实例间隔几秒起，避免同时启动的瞬时资源争用。真正会卡死的是 SysV shm
        # 段耗尽（见上 _cleanup_pi_shm）——崩溃实例泄漏的僵尸段累积占满 shmmni(32) 后，新
        # 实例 QSharedMemory::create 失败、启动即崩、无 done 标记（实测固定卡死后半批 worker）。
        # stagger 只缓解瞬时争用，真根因靠跑前清残留 + 余量降并发解决。最后一个不用等。
        if i < len(shards) - 1 and args.stagger > 0:
            time.sleep(args.stagger)
    print(f"起了 {n} 个 PixInsight instance（slot {args.slot_base}..{args.slot_base+n-1}，"
          f"错开 {args.stagger}s 启动），各处理 ~{len(shards[0])} 张（不 pkill，与 GUI 并存）", flush=True)

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

    # 收尾：先 terminate 本次起的实例，等它们退出后清残留 shm 段（防泄漏累积占满配额，
    # 否则下次跑会触顶。每个 PI 实例占 1 段，正常退出会自己释放，被 terminate/崩溃的不会）。
    for p in procs_running:
        if p.poll() is None:
            p.terminate()
    for p in procs_running:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    time.sleep(1)
    freed = _cleanup_pi_shm()
    if freed:
        print(f"收尾清理了 {freed} 个残留 shm 段", flush=True)
    ok = sum(open(d).read().count("OK ") for d in done_paths if os.path.isfile(d))
    err = sum(open(d).read().count("ERR ") for d in done_paths if os.path.isfile(d))
    # 累积本次 OK 到持久 done log（供 --resume 续传）。append 不覆盖，跨多次运行累积。
    with open(persist_done, "a") as pf:
        for d in done_paths:
            if os.path.isfile(d):
                for ln in open(d):
                    if ln.startswith("OK "):
                        pf.write(ln)
    print(f"DONE: {ok} 成功, {err} 失败 / {len(files)} 张（done log: {persist_done}）", flush=True)
    if err:
        for d in done_paths:
            if os.path.isfile(d):
                for ln in open(d):
                    if ln.startswith("ERR"):
                        print("  " + ln.strip())


if __name__ == "__main__":
    main()
