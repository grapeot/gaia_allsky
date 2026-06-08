# Gaia 全天星图渲染

用真实恒星星表（Gaia DR3）渲染全天星空。核心不在 reproduce（算得跟照片一样没价值），而在 **knob**——
模拟把"现实中看不到的东西"做成可以拧的旋钮：调光污染、调眼睛敏感度、调宇宙位置。

## 由来

源自 turquoise_band（月食模拟）项目里一个物理正确的星场渲染管线：
- **星等 → 亮度**：`L = 10^(-0.4·(m − m_ref))`，物理正确的相对亮度。
- **B-V 色指数 → 星色**：恒星色温（蓝白星 vs 橙红星），真实星色。

那个管线在一小片天区（金牛座）上验证过。本项目把它推到**全天 Gaia 星表**——如果管线对，
银河（恒星密度最高的带）应该自然涌现，不需要任何"画银河"的特殊处理。银河涌现验证了管线，
而管线对了，knob 才有意义：拧的每一个旋钮背后都是真实物理。

## 三个 knob

我们日常看到的星空，是被三层东西过滤后的残影。模拟把这三层过滤器做成旋钮：

- **光污染**（`render_horizon.skyglow_level`）：additive 天空辉光模型，Bortle 1（荒漠）→9（市中心）。
  辉光是加性背景，按各级天空面亮度 μ(mag/arcsec²) 转线性叠加。看银河从清晰到被城市天光彻底淹没——
  倒过来就是"暗空旅行找回银河"。`outputs/knob_light_pollution.png`，依据见 `docs/bortle_skyglow.md`。
- **眼睛敏感度**（`gain` / 星等 cutoff）：肉眼极限 NELM 6 等（正常人眼）→11 等（超人眼/Gaia 极限）。
  银河从朦胧光带解析成连续星海——"银河一直在，是你的眼睛不够格"。`outputs/knob_eye_sensitivity.png`。
- **宇宙位置**（`render_3d`）：Gaia 视差→3D 笛卡尔，观测者平移重投影（平方反比变亮 + 几何重投影）。
  飞出几百 pc，近处星座（北斗）散架，银河带不动（星座是视角幻觉，银河是大尺度结构）。

## L 型飞行视频与数据边界

`render_l_video` 的 L 型轨迹分两段，第二段撞到一个诚实的边界：

- **第一段**沿银道飞 ~400pc（全天 equirectangular）：北斗散架、银河不动。
- **第二段**垂直飞出 + 镜头下俯（鱼眼）：整个星空收缩成脚下一个发光的球。

第二段本想做"飞出银盘看银河变盘"，但物理上做不成——**Gaia 用可见光视差，只能精确测到太阳周围
一个被尘埃限制的有限球，这个球不是银盘**。飞出去看到的不是银河盘，是钻出数据球、看见球内壁。
银河真身（几千 pc 外）可见光视差够不着，得靠射电（21cm）、脉泽甚长基线、红外测——这也正是
人类其实**没有银河系俯视照片**、那些俯视图都是反演 + 想象图的原因。

这把 turquoise_band 那篇 thesis（算法的近似要诚实）推进一层：**数据的边界也要诚实**——
模拟再强，也变不出数据里没有的东西。能让北斗散架（数据可靠），变不出银河盘（数据够不着）。

## 银河涌现（管线验证）

银河从逐颗真实恒星自然涌现，零特殊处理，验证第一性原理管线正确：

- **低分辨率 SDR**（G<8，6.3 万颗）：银道面恒星密度显著高于高银纬，银河带可辨。
- **高分辨率 SDR**（G<11，124 万颗）：`outputs/gaia_g11_mollweide.png`。银河带、银心方向的
  暗尘埃裂缝（Great Rift）清晰。
- **HDR**（16bit TIFF）：动态范围约 **17 stops**——SDR 8bit 只有 8 stops，必须在"银心过曝"和
  "暗星淹没"间二选一；HDR 同时保留两端。tonemap 默认 **log 编码（数字底片式）**而非纯 linear：
  暗部（恒星主体）展开、亮部 rolloff，信息密度在 16bit 里均匀，保留足够原始信息供后期 PS/grade。

## tonemap（`normalize_brightness`）

三条曲线，按用途选：
- `linear` — 纯线性，暗部信息被挤压，仅在需要原始线性时用。
- `gamma` — 幂律，SDR 常用，温和抬暗部。
- `log` — 数字底片式，暗部大幅展开 + 亮部 rolloff，最适合 16bit HDR 的"底片"（后期再 grade 成成片）。

## 投影

- `mollweide` — 全天椭圆，印刷品/科普图。
- `equirectangular` — 全天矩形 2:1，VR 球面贴图标准格式（银道坐标=银河横平 / 赤道 / 地平坐标）。
- 地平坐标（`render_horizon`）— 站在地面、地球透明、平视即地平线的真实视角，银河斜挂天上。
- 鱼眼方位（`render_3d.render_fisheye_lookdir`）— "飞出去回望"，星空收缩成脚下的球。

## 结构

```
src/
  render_starmap.py   基础: 星等→亮度, B-V→星色, 投影, tonemap(linear/gamma/log), accumulate_stars
  render_horizon.py   地平坐标 + skyglow 光污染模型
  render_3d.py        3D reproject(视差→笛卡尔, 平移重投影), 鱼眼下俯, blooming, L 轨迹
  render_l_video.py   L 飞行视频 + ffmpeg 合成
  render_vr_video.py  纯 equirectangular VR 飞行视频 CLI
  render_big_dipper_video.py  朝北斗方向飞行的前向鱼眼视频 CLI
  video_common.py     多进程逐帧渲染 + 帧落盘 + SDR mp4 合成
  fetch_gaia_3d.py    带 parallax 子集获取
tests/test_render.py  18 个物理正确性测试
data/raw/             Gaia 子集缓存(gitignore)
outputs/              渲染图/视频(gitignore)
docs/                 bortle_skyglow.md 等
```

## 两版视频 CLI

两个 CLI 都是先并行渲染 PNG 帧，再用 ffmpeg 合成 SDR H.264 mp4；帧目录默认保留。
`--workers` 默认使用本机全部 CPU 核心，可按内存或 I/O 情况手动降低。

低分辨率预览：

```bash
python src/render_vr_video.py \
  --width 640 --height 320 --frames 60 --fps 30 --workers 32 \
  --frames-dir outputs/vr_equirect_lowres_frames \
  --output outputs/vr_equirect_lowres.mp4

python src/render_big_dipper_video.py \
  --width 640 --height 640 --frames 60 --fps 30 --workers 32 \
  --frames-dir outputs/big_dipper_forward_lowres_frames \
  --output outputs/big_dipper_forward_lowres.mp4
```

高分辨率 VR 可直接把 VR 版本调到 2:1，例如 `--width 8192 --height 4096`。前向版本默认朝北斗七星中心方向看并沿同方向飞，`--look-dir x,y,z` 和 `--flight-dir x,y,z` 可覆盖。

## 不做

- 不做实时/交互。
- 不追求测光级精确（先看银河定性涌现）。
- 不变出数据里没有的东西（银河盘俯视、星云气体——前者数据够不着, 后者非恒星不在管线内）。
