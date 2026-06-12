# Skills —— 可复用渲染工作流

Gaia all-sky 项目里反复用到的大规模渲染 workflow，记录命令、坑、和手动步骤，
方便下次（或别的 agent）直接复现，不用重新摸索。

## 通用前提

- 所有渲染先 `source .venv/bin/activate`（uv venv）。
- **后台跑必须用 wrapper 脚本，内部 `source venv`**：`nohup python ...` 不继承当前
  shell 的 venv，会 `python: No such file or directory` 静默失败（踩过两次）。
- **一次只跑一个大渲染 job，彼此串行**（都吃几十～几百 GB 内存）。内部可并行（--workers）。
- 当前默认数据源 `data/raw/fov_g20_bsc5.npz`（6.16 亿 Gaia + 20 颗 BSC5 亮星）。
- **渲完图必 plot histogram 和参照对分位（p99/中位），对不上别往下**。别信缩略图肉眼。

## 工作流清单

- [hips_1b_tile_generation.md](hips_1b_tile_generation.md) —— 十亿像素 HiPS 瓦片生成
  （render_tan_wcs --tiles → PixInsight 调色 → hipsgen 拼金字塔 → Aladin Lite 部署）。
  含超大图的两个致命物理 bug（sum vs mean 池化、Weber 误杀乳光）。

- [zoom15_video_workflow.md](zoom15_video_workflow.md) —— 全景→银心 zoom-in 视频
  （两段渲帧 → PixInsight 批处理调色 → ffmpeg 带首尾停顿合成 H.265/hvc1 QuickTime 兼容）。

- [render_12k_static.md](render_12k_static.md) —— 12K 单图静态渲染 + 16-bit TIFF 导出
  （render_fov --save-linear → 从线性画布重 tone → tifffile 出 16-bit）。

- [ablation_study_rendering.md](ablation_study_rendering.md) —— principles 页消融实验 11 张图
  （暗空阶梯 reproduce 主图 + step4 数据深度切换器 + step5 Weber 对比对走 sweep 路径）。

- [bestpractice_pixinsight_batch.md](bestpractice_pixinsight_batch.md) —— PixInsight 命令行
  **并行**批处理（`tools/pixinsight_batch.py`）：解析 .xpsm 重建 process，多 instance 用高
  slot 200 并行（不 pkill、与 GUI 并存）。把 zoom 帧/HiPS 瓦片批量调色从手动变全自动。

## 资产

- `batch_process_frames.xpsm` —— PixInsight process icon（CurvesTransformation×2 + SCNR），
  zoom 帧/HiPS 瓦片批量调色用。可手动（GUI 拖回桌面）或自动（`tools/pixinsight_batch.py`）。
