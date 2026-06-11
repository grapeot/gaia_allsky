# Skill: zoom15 视频工作流（渲帧 → PixInsight 批处理 → 合成）

全景 → 银心核 1:1 的 15 秒 zoom-in 视频。每帧是固定切点、fov 指数缩小的 TAN 投影，
立体角归一化保证 zoom 时亮度一致（不忽明忽暗）。

完整链路三步：**① 渲帧（我做）→ ② PixInsight 批处理调色（用户做）→ ③ ffmpeg 合成（我做）**。

## ① 渲帧

zoom15 = 两段，共 450 帧 @30fps = 15 秒：
- seg1：70°→1.9°，10 秒，300 帧（f0000–f0299）
- seg2：1.9°→0.25°，5 秒，150 帧（f0300–f0449）

两段分别用 `--fov-start/--fov-end/--seconds` 跑，再把 seg2 重编号 f0300+ 合并到一个
帧目录。切点 lc=0, bc=-2，size 640。用 wrapper 脚本（内部 `source .venv/bin/activate`，
否则 nohup 丢 venv 静默失败）：

```bash
# seg1: 70°→1.9° over 10s (300 帧)
python src/render_zoom_video.py --data data/raw/fov_g20_bsc5.npz \
  --out /tmp/_seg1.mp4 --lc 0 --bc -2 \
  --fov-start 70 --fov-end 1.9 --size 640 --seconds 10 --fps 30 --workers 8 \
  --frames-dir /tmp/zoom_seg1 --keep-frames
# seg2: 1.9°→0.25° over 5s (150 帧)
python src/render_zoom_video.py --data data/raw/fov_g20_bsc5.npz \
  --out /tmp/_seg2.mp4 --lc 0 --bc -2 \
  --fov-start 1.9 --fov-end 0.25 --size 640 --seconds 5 --fps 30 --workers 8 \
  --frames-dir /tmp/zoom_seg2 --keep-frames
# 合并 seg1 f0000-0299 + seg2 重编号 f0300-0449 → outputs/zoom15_frames_bsc5/
```

为什么分两段不是一条连续 70°→0.25° sweep：两段在 10 秒处有**速率变化**（后段 zoom 更快），
单段等比 sweep 是匀速的，观感不同。保留两段结构才匹配既定版本。

帧是边渲边写到最终路径的（每个 worker 自己即时 `Image.save`），中途崩只丢未渲的、
已渲留盘；进度逐帧打印（PR #31 起）。

## ② PixInsight 批处理调色（用户做）

渲出的原始帧偏暗、色彩平淡。用户在 PixInsight 里批量过一个 process icon 统一处理：

- process icon：`skills/batch_process_frames.xpsm`（PixInsight 1.9.3 XPSM 格式）。
- 内容：一个 **ProcessContainer**，含 **CurvesTransformation ×2**（曲线提亮/调对比/
  调 RGB 通道）+ **SCNR**（去绿噪）。
- 用法：PixInsight 里 File → Open 这个 .xpsm 把 process icon 拖回桌面，再用
  **ImageContainer / Batch 方式**把 `zoom15_frames_bsc5/` 整个目录的帧批量过这个容器，
  输出覆盖回同名帧（或写到新目录后替换）。
- 处理后帧色彩更鲜明饱和、银心暖金更突出。

> 这一步是手动的、在 PixInsight GUI 里做。我（agent）渲完原始帧后停下，等用户
> 批处理完，再继续合成。处理后的帧时间戳会更新，可据此确认是新版。

## ③ ffmpeg 合成（带首尾停顿，H.265 QuickTime 兼容）

终版规格：**19 秒 = 首停 1s + zoom 15s + 尾停 3s**，H.265 / hvc1 / CRF24。

```bash
ffmpeg -y -framerate 30 -i outputs/zoom15_frames_bsc5/f%04d.png \
  -vf "tpad=start_mode=clone:start_duration=1:stop_mode=clone:stop_duration=3" \
  -c:v libx265 -crf 24 -pix_fmt yuv420p -tag:v hvc1 \
  outputs/zoom15_bsc5_h265_hold.mp4
```

**QuickTime 兼容三要素：**
- **`-tag:v hvc1`** —— 关键。QuickTime 只认 `hvc1` tag，不认 ffmpeg 默认的 `hev1`，
  否则 QuickTime 打不开（Chrome/Firefox 也常不解码 hev1）。
- `-pix_fmt yuv420p` —— 兼容性像素格式。
- mp4 容器。
- `tpad` 滤镜：`start_mode=clone:start_duration=1` 复制首帧停 1 秒，
  `stop_mode=clone:stop_duration=3` 复制末帧停 3 秒。450 帧 → 570 帧。

验证：`ffprobe` 看 `hevc (Main) (hvc1)` + `Duration 00:00:19`。

## 网页嵌入版（如需）

主页用的是 H.264 重编码版（HEVC 在 Chrome/Firefox 不解码故转码），640² 方形：
```bash
ffmpeg -y -i outputs/zoom15_bsc5_h265_hold.mp4 \
  -c:v libx264 -pix_fmt yuv420p -crf 18 outputs/zoom_milkyway.mp4
```

## 相关文件

- `src/render_zoom_video.py` — 帧渲染（复用 render_tan_wcs.render_tile）
- `skills/batch_process_frames.xpsm` — PixInsight 批处理 process icon
- `outputs/zoom15_frames_bsc5/` — 当前 BSC5 帧（用户 PixInsight 处理过）
- `outputs/zoom15_bsc5_h265_hold.mp4` — 当前终版视频
