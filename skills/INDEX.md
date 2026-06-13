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

- [hips_1b_tile_generation.md](hips_1b_tile_generation.md) —— 十亿~几百亿像素 HiPS 瓦片生成
  （**全天 tone 标定 → render_tan_wcs --tiles --calib → PixInsight 调色 → hipsgen 拼金字塔
  → Aladin Lite 部署**）。一条龙脚本 `tools/build_hips_pipeline.sh`。含：
  全天 tone 标定复刻 hero 对比且块间无接缝（calibrate_alltile_tone.py）、
  HEALPix 分桶 memory-aware（build_healpix_bucketed.py，每 tile 只读邻桶，让高分辨率/全天可行）、
  亚度文件名碰撞修复、hipsgen `hips_order` 高分辨率必加坑（不限会过采样数倍时间）、
  PixInsight shm 段耗尽真根因 + `--resume` 断点续传、Allsky zoom-out 糊、超大图两个物理 bug。
  全天数据：build_allsky_manifest.py（Flatiron 全天下载清单）+ build_fov_deep_cache --all-sky。
  换机渲染：大规模时 sync 整个 repo 到更强机器跑（不打包，见 3.4），分桶 npz 16G + 代码，比传 tiles 轻。

- [zoom15_video_workflow.md](zoom15_video_workflow.md) —— 全景→银心 zoom-in 视频
  （两段渲帧 → PixInsight 批处理调色 → ffmpeg 带首尾停顿合成 H.265/hvc1 QuickTime 兼容）。

- [render_12k_static.md](render_12k_static.md) —— 12K 单图静态渲染 + 16-bit TIFF 导出
  （render_fov --save-linear → 从线性画布重 tone → tifffile 出 16-bit）。

- [ablation_study_rendering.md](ablation_study_rendering.md) —— principles 页消融实验 11 张图
  （暗空阶梯 reproduce 主图 + step4 数据深度切换器 + step5 Weber 对比对走 sweep 路径）。

- [bestpractice_pixinsight_batch.md](bestpractice_pixinsight_batch.md) —— PixInsight 命令行
  **并行**批处理（`tools/pixinsight_batch.py`）：解析 .xpsm 重建 process，多 instance 用高
  slot 200 并行（不 pkill、与 GUI 并存）。把 zoom 帧/HiPS 瓦片批量调色从手动变全自动。
  含：SysV shm 段耗尽真根因（「卡死一半 worker」）+ 内置防泄漏、`--resume` 断点续传
  （走 done log，不靠 mtime）。

## 资产

- `batch_process_frames.xpsm` —— PixInsight process icon（CurvesTransformation×2 + SCNR），
  zoom 帧/HiPS 瓦片批量调色用。可手动（GUI 拖回桌面）或自动（`tools/pixinsight_batch.py`）。
