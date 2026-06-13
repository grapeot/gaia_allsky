# Skill: PixInsight 命令行并行批处理

## 元数据

- **类型**: BestPractice / 工具
- **适用场景**: 要把一个 PixInsight 处理流程（process icon）批量、自动地应用到一大批图（几十到上千张），且想并行加速
- **CLI**: `tools/pixinsight_batch.py`
- **创建日期**: 2026-06-12

## 目标

把一个在 PixInsight GUI 里调好的 process icon（导出为 `.xpsm`）自动应用到一个目录里
的所有图，无需手动逐张操作。用多 PixInsight instance 并行加速（单实例难并行，多实例
各占一个 application slot 可并行）。

角色分工：**process icon (`.xpsm`) 只负责"明确参数"**（在 GUI 里调好、导出）；**实际
批量执行由 CLI 自动完成**——解析 xpsm 拿到 process 链，在 headless PixInsight 里用 JS
重建并 `executeOn` 每张图。

## 用法

```bash
# 输出到新目录（保留原图）
python tools/pixinsight_batch.py --xpsm <icon.xpsm> --in <图目录> --out <输出目录> --workers 10
# 原地覆盖
python tools/pixinsight_batch.py --xpsm <icon.xpsm> --in <图目录> --in-place --workers 10
```

`--in` 目录里所有 `*.png`（`--pattern` 可改）被处理。`--workers N` = 并行 instance 数。

## 验收标准

- CLI 输出 `DONE: X 成功, 0 失败 / X 张`，X = 输入图数。有失败会逐条打印 `ERR <file> : <异常>`。
- 抽一张图前后对比，处理效果与 process icon 的设计一致（如 xpsm 调了蓝通道+亮度，则
  处理后 B 通道均值上升、整体变亮）。**务必抽验，不要只看"成功"计数**——成功 ≠ 效果对。
- 并行有效：N worker 的总耗时应明显短于串行（实测 6 张 2 worker 20s vs 串行约 40s）。

## 已知陷阱（实测踩过）

| 陷阱 | 表现 | 应对 |
|------|------|------|
| **SysV shm 段耗尽（「卡死一半 worker」的真根因）** | 后半批 worker 无 done 标记、PI 进程秒退、表现为「固定卡死 N 个」；手动单跑报 `*** Fatal Error: ... QSharedMemory::create: out of resources` | 每个 PI 实例占 1 个 SysV 共享内存段，macOS 全局上限 `kern.sysv.shmmni=32`。崩溃/被 kill 的实例**泄漏僵尸段不释放**（`ipcs -m -o` 看 NATTCH=0、key 前缀 `0x510f`），反复跑累积占满 32 → 新实例 `QSharedMemory::create` 失败、启动即崩。**误判过 slot 冲突/WebEngine 竞争，都不是。** 工具已内置：跑前 `_cleanup_pi_shm()` 清无附着 PI 段 + 余量不足自动降并发 + 收尾 terminate 后清理。手动清（确认 PI 全退）：`ipcs -m \| awk '$1=="m"&&$5=="<user>"{print $2}' \| xargs -n1 ipcrm -m`；抬上限：`sudo sysctl -w kern.sysv.shmmni=128` |
| **不指定 slot → worker yield 给已运行实例** | worker JS 完全不执行、无日志、无产物，进程秒退 | 裸 `-n`（不指定 slot）会 yield 给已运行实例、脚本不跑。**解法是用高 slot（`-n=<200+>`）起独立实例**，不是 pkill——实测高 slot 实例与 GUI/其它任务**完美并存、互不 yield、互不干扰**。CLI 默认 `--slot-base 200`；多个 batch 并行给不同基址（如 200/220）。**不要 pkill**（会杀 GUI、阻止并存，是过度方案） |
| **`--force-exit` 抢跑** | 脚本没执行就退出 | `-r` 脚本"just after startup"执行，但 `--force-exit` 会抢在前面退出。**不要用 `--force-exit`**；worker JS 末尾自己 `Console.terminate()` 退出 |
| **xpsm 命名空间** | 正则/ET 解析匹配不到 process 类 | xpsm 有 `xmlns="http://www.pixinsight.com/xpsm"`，ElementTree 解析要带 ns 前缀 |
| **`enabled="false"` 的 process** | 把 GUI 里禁用的步骤也应用了 | xpsm 里 `enabled="false"` 是 GUI 禁用的，解析时跳过 |
| **headless 启动慢/有波动** | 等 25s 无日志就以为失败 | PixInsight headless 起 10-16s，但有波动。轮询 done 标记而非固定等待；worker JS 第一行就写日志确认执行 |
| **GUI 被一起杀** | 清 slot 的 pkill 会杀掉你 GUI 开着的 PixInsight | 这是清 slot 的代价，无法只杀残留留 GUI。批处理前提醒用户保存 GUI 工作 |

## 边界与扩展

- CLI 目前只重建 **CurvesTransformation** 和 **SCNR** 两种 process（本项目的 xpsm 用到的）。
  xpsm 里有其它 process 类型会报错 `未支持的 process 类型 X`——需在 `procs_to_js()` 里加
  对应的 `new <Process>` 重建逻辑（PJSR 类名 + 参数，参考 PixInsight `src/scripts/` 示例）。
- `executeOn(view, false)` 的 `false` = no swap file，省时；批处理用它。
- 依赖：PixInsight 装在 `/Applications/PixInsight/`（macOS）。CLI 里 `PI` 路径写死，换平台改它。
- PJSR API 参考：本机 `/Applications/PixInsight/src/scripts/`（大量实例脚本）+ pidoc。
  读 process 用 `ProcessInstance.fromIcon(id)`（需 icon 已在工作区）；本 CLI 不走这条，
  改为解析 xpsm + JS 里 `new <Process>` 重建（不依赖 icon 加载、参数完全可控）。

## 输出规格

处理后的图写到 `--out` 目录（同名）或原地覆盖（`--in-place`），格式同输入。
worker 中间产物在 `/tmp/pi_batch_work/`（worker_i.js + done_i.log），可看 done 日志排查。
