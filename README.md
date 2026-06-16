# Gaia 全天星空实验

把欧洲航天局 Gaia DR3 星表里上亿颗实测恒星，一颗一个点画回天球——没有银河贴图，银河自己从星密度里浮现。

[在线预览](https://grapeot.github.io/gaia_allsky/)
| [渲染原理](https://grapeot.github.io/gaia_allsky/principles.html)
| [English](README-en.md)

## 这是什么

本项目用 Gaia 卫星实测的 18 亿颗恒星数据，将在天区内的真实观测者逐一投射到屏幕上：每颗星保留它在星表里的位置、星等和色指数，不叠加任何银河贴图或尘埃素材。当你把光污染从顶级暗空逐级加到大城市中心，银河的光带会真实地一节节消退——不是视觉特效，是天空背景亮度盖过了弥散光对比度。再把飞行视角推出太阳系，北斗七星几十光年内就散了架，但银河的宏观结构纹丝不动。代码、渲染参数和数据处理脚本全部公开，所有效果均可复现。

## 快速开始

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest tests/ -q
```

测试不依赖完整 Gaia 数据；缺大星表时少数银河密度集成测试会自动跳过。

## 数据获取

星表数据和渲染输出均不提交 Git。正式渲染使用 Flatiron bulk mirror 的 Gaia DR3 分片（共 2044 个 gzip，约 412 GiB），经本地过滤后缓存为 NPZ，详细流程见 `docs/gaia_catalog_usage.md`。

轻量体验只需下载 G<11 星表缓存（约 125 万颗）：

```bash
python src/fetch_gaia_allsky.py --gmax 11 --output data/raw/gaia_g11.npz
```

## 复现效果

按 Norder 分层生成 HiPS 时，可以把重投影和最终组装分开跑。比如低层 `3 4 5 6` 已完成，只补高层 `7 8` 后，先跑：

```bash
bash tools/render_per_order_pipeline.sh allsky_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz --range 0,360 -90,90 "7 8" --workers 30 --hipsgen-par 1 --hipsgen-th 30 --step-frac 1.0 --hipsgen-only
```

等 `7 8` 完成后，只组装已有各层，不重跑 Python 渲染或 Java `hipsgen`：

```bash
bash tools/render_per_order_pipeline.sh allsky_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz --range 0,360 -90,90 "3 4 5 6 7 8" --assemble-only
```

Bortle 1–9 银河消失序列：

```bash
python src/render_bortle_eye_grid.py --bortles 1,2,3,4,5,6,7,8,9 --eye-deltas 0 --columns-per-row 3 --output outputs/bortle_scale.png
```

飞行视频（前向透视）：

```bash
python src/render_big_dipper_video.py --width 2160 --height 2160 --duration 10 --fps 60 --workers 32
```

全景视频（VR equirectangular）：

```bash
python src/render_vr_video.py --width 4096 --height 2048 --duration 10 --fps 60 --workers 32
```

深星表单图渲染（约 6.16 亿颗 G<20 真实恒星）：

```bash
python src/build_fov_deep_cache.py --gmax 20 --out data/raw/fov_g20.npz --workers 16
python src/render_fov.py --data data/raw/fov_g20.npz --out outputs/fov_g20.png --faint-gain 1.0 --workers 28
```

## 目录结构

```
src/
  render_starmap.py              星等色指数、全天投影、PSF 卷积、tone mapping
  render_horizon.py              地平坐标变换与 Bortle skyglow 模型
  render_bortle_eye_grid.py      Bortle × 灵敏度对比图入口
  render_3d.py                   Gaia 视差 3D 重投影
  render_big_dipper_video.py     前向透视飞行视频
  render_vr_video.py             VR 全景飞行视频
  render_fov.py                  亿级星表并行单图渲染
  render_tan_wcs.py              TAN 投影 + WCS 输出，衔接 HiPS 瓦片管线
  video_common.py                并行逐帧渲染、ffmpeg 合成
  motion.py                      共享 L 型飞行轨迹
  fetch_gaia_allsky.py           Gaia 全天星表获取（按星等区间分片查询）
  fetch_gaia_3d.py               近邻 3D 子集获取
  tone_iterate.py                在线性画布上秒级迭代 tone mapping
  build_fov_deep_cache.py        并行解压 Flatiron 分片构建深星表缓存
docs/
  prd.md                         科学目标与成功标准
  rfc.md                         渲染管线设计文档
  working.md                     历史决策与调参记录
  gaia_catalog_usage.md          星表获取与缓存约定
  bortle_skyglow.md              Bortle/SQM/NELM 参考表
tests/
  test_render.py                 物理、投影、运动、tone map 及 CLI 语义测试
```

## 科学边界

本项目追求定性正确与可解释，不追求测光级精度。物理模型在公开数据范围内逐层锚定：星等通过 Pogson 公式换算亮度，色指数经 Pecaut & Mamajek 主序星标定转换为表面温度再经黑体谱积分得到 sRGB，太阳（G2V，5772K）被锚定为中性白。显示层与物理层严格分离——星星的物理亮度由公式决定，屏幕观感由显式暴露的显示参数控制，后者从不混入天体物理常量。

Gaia 视差测量精度随距离衰减，只有太阳周围几千光年内的恒星距离可靠。飞行视频里飞得越远星越稀疏，那不是宇宙的尽头，是测量的尽头。本项目可以演示星座散架和局部星场重投影，但无法生成一张真正的银河俯视图——人类至今也没有那样的照片。

渲染不引入星云模型、星际尘埃模型或银河贴图。银河的乳光来自海量暗星的疏密分布，尘埃暗带来自那方向真实恒星的缺失计数，全部是数据自身的正负信息。

屏幕是 SDR 设备，真实夜空最亮星与最暗银河纹理之间上百万倍的亮度差必须压缩映射后才能显示。画面保留了"谁比谁亮"的相对关系，但不保证绝对亮度的测光比例。看结构、看趋势、看对比，这些图可靠；拿去量星等的绝对值，不行。

