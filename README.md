# Gaia 全天星空实验

城市里看不到银河。城市灯光把夜空背景亮度抬高了几十倍到约 60 倍，在一个亮得发灰的天空背景下，人眼分辨不出银河的微弱结构。每年夏夜银心照样从南方地平线升起，星星的数量没有变。银河一直在那里，但人眼能看到的是被天空背景和人眼灵敏度共同过滤之后的结果。

欧洲航天局的 Gaia 卫星发布了迄今最精确的恒星星表。DR3 版本记录了近 18 亿颗恒星的位置、星等和颜色。这个仓库把这些恒星按真实数据一颗一颗画到天球上。画上的星数量足够多以后，银河会以大尺度恒星密度分布的形式直接显现，不需要单独绘制的银河贴图。

正因为每一颗星的亮度和颜色都锚定在 Gaia 物理数据上，我们可以把影响观测的变量做成可调的物理旋钮：光污染程度（Bortle 暗空等级）、人眼灵敏度（NELM，裸眼极限星等）、以及观测者在银河系里的三维位置。每个调节项对应一个物理观测条件，调整范围和默认值都有公开的数据依据。

## 可以看到什么

正式结果有三类，每类回答一个关于星空的直觉问题。

第一类是一张矩阵对比图，把光污染旋钮和人眼灵敏度旋钮交叉组合。它用广州纬度的地平视角渲染银河，让你同时看到 Bortle 暗空等级变化和有效极限星等（NELM）变化的组合效果。这里的 NELM 由人眼和天空背景共同决定——它随天空变暗而升高，随光污染加重而降低。Bortle 1 顶级暗空的经验值可以到 7.6–8.0 等；常说的"肉眼 6 等"更接近普通暗天的口径，不是人眼的物理上限。

第二类是一张 Bortle 1 到 9 的银河消失序列。很多爱好者有过这样的体验：暗空保护区里，银河一眼可见；回到城市，同一片天空还在头顶，却什么都看不到。这张图把九个等级放在同一张画面里，用统一的亮度基准来比较，展示天空背景逐级升高后银河如何变淡直至消失。

第三类是两部星际飞行视频。VR 版输出全天 equirectangular 球面画面，适合在手机或 VR 设备里转头看；前向透视版是一段朝北斗方向出发、再转向银盘的普通镜头飞行。从视频里可以看到：星座是太阳系所在位置的二维投影，近处恒星随着观测者移动几十 pc（parsec，秒差距）就会散架；银河来自更大尺度的恒星分布，不会因为这点移动而消失。

项目的 GitHub Pages 会放压缩后的结果图、视频预览和下载入口；代码仓库负责解释模型、复现方法和二次开发接口。

## 科学边界

这个项目追求的是定性正确和可解释，不追求测光级精确。物理模型部分已经按公开数据完成，视觉层仍然是工程显示，不是对人眼的光谱计量模拟。

已经有物理锚点的部分：

- 星等到亮度：`L = 10^(-0.4 * (m - m_ref))`
- BP-RP/B-V 简化色指数到恒星颜色：蓝白星、太阳型星、橙红星有可见差异
- Gaia 银道坐标、赤道坐标和地平坐标投影
- Bortle 暗空等级到天空背景面亮度的相对梯度
- 3D 视差重投影和平方反比亮度变化
- 经验 Bortle/NELM 表：Bortle 1 约 7.8，Bortle 6 约 5.3

显示层仍然需要 tone mapping（亮度映射）。屏幕是 SDR（标准动态范围），人眼暗视觉也不是线性相机。正式视觉图使用固定 sky floor、共享 reference stretch、双层 PSF（点扩散函数）来确保结果在网页和缩略图里可读。这些显示参数在代码里显式暴露，不会混进物理模型。换句话说：星星的物理亮度由公式决定，屏幕观感由显示参数控制，两套体系各自独立。

还有一个重要边界：Gaia 可见光视差只覆盖太阳附近一个有限数据球，不是整个银河盘面。因此本项目能诚实地演示星座散架、局部星场重投影和数据球边界，但不能生成一张真正的银河俯视图。人类本来也没有那种照片；网上常见的银河俯视图是多波段观测加模型反演得到的想象图，不是任何相机拍出来的。

## 快速开始

本项目使用 Python 3.12。推荐使用 `uv` 创建虚拟环境。

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest tests/ -q
```

测试不依赖下载完整 Gaia 数据；缺少大星表时，少数银河密度集成测试会自动跳过。

## 数据获取

仓库不会提交 Gaia 原始缓存和渲染输出。`data/raw/` 和 `outputs/` 都被 `.gitignore` 排除。

3D 飞行所需的近邻恒星子集可用：

```bash
python src/fetch_gaia_3d.py
```

全天 G<11 星表可用下面的命令生成：

```bash
python src/fetch_gaia_allsky.py --gmax 11 --output data/raw/gaia_g11.npz
```

输出文件字段包括：

```text
l, b, g, bp_rp
```

其中 `l/b` 是银道坐标，`g` 是 Gaia G 星等，`bp_rp` 用于近似恒星颜色。这个缓存约百万颗星，属于本地数据文件，不进入 Git 历史。

## 复现正式图片

光污染和灵敏度代价主图：

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,6 \
  --eye-deltas 0,2,4 \
  --output outputs/knob_bortle_eye_grid.png
```

Bortle 1–9 银河消失序列：

```bash
python src/render_bortle_eye_grid.py \
  --bortles 1,2,3,4,5,6,7,8,9 \
  --eye-deltas 0 \
  --columns-per-row 3 \
  --reference-bortle 1 \
  --reference-value 2 \
  --output outputs/knob_bortle_scale_grid.png
```

这两张图使用同一套 normalization 思路：每个 panel 单独适应 sky floor，但整张 grid 共享一个显示 reference。这样暗空图不会浪费 SDR 动态范围，高光污染 panel 也不会被各自拉亮。

## 复现正式视频

VR equirectangular 版本：

```bash
python src/render_vr_video.py \
  --width 4096 --height 2048 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/vr_equirect_hires_frames \
  --output outputs/vr_equirect_hires.mp4
```

前向透视版本：

```bash
python src/render_big_dipper_video.py \
  --width 2160 --height 2160 --duration 10 --fps 60 --workers 32 \
  --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
  --frames-dir outputs/big_dipper_forward_hires_frames \
  --output outputs/big_dipper_forward_hires.mp4
```

两个视频都会先并行渲染 PNG 帧，再用 ffmpeg 合成 SDR H.264 mp4。帧目录默认保留，便于检查和重新编码。
当前默认辉光强度偏克制：VR equirectangular 为 `--bloom-strength 0.25`，前向透视为 `--bloom-strength 0.175`。

## 代码结构

```text
src/
  render_starmap.py            星等、星色、全天投影、SDR/HDR tone map
  render_horizon.py            地平坐标和 Bortle skyglow 模型
  render_bortle_eye_grid.py    Bortle × NELM 视觉对比图
  render_3d.py                 Gaia 视差 3D 重投影
  motion.py                    共享 L 型飞行轨迹
  video_common.py              并行逐帧渲染、PNG/TIFF 帧、ffmpeg 合成
  render_vr_video.py           VR equirectangular 视频入口
  render_big_dipper_video.py   前向透视视频入口
  fetch_gaia_allsky.py         Gaia 全天 G 星等缓存获取
  fetch_gaia_3d.py             Gaia 近邻 3D 子集获取
tests/
  test_render.py               物理、投影、运动、tone map 和 CLI 语义测试
docs/
  prd.md                       科学目标和成功标准
  rfc.md                       实现设计和边界
  test.md                      测试策略
  bortle_skyglow.md            Bortle/SQM/NELM 参考表
```

## 开发者和 AI 工具使用说明

如果你是接手这个 repo 的 AI 编程 agent，先读：

1. `README.md`：项目入口和复现命令
2. `docs/prd.md`：这个项目到底要解释什么
3. `docs/rfc.md`：渲染管线和关键设计取舍
4. `docs/test.md`：什么算验证完成
5. `docs/working.md`：历史决策和调参记录

不要把 `data/raw/` 或 `outputs/` 里的大文件提交进 Git。公开页面需要的图片和视频，应放到 GitHub Pages 使用的压缩资产目录里，而不是直接提交完整渲染缓存。

## 隐私与发布检查

这个仓库按公开发布标准整理，只使用公开科学数据，不需要真实凭证。正常渲染和测试不需要 API key。Gaia 数据本身公开，但大体积本地缓存和生成输出会被 `.gitignore` 排除。

发布前仍然需要跑一次 privacy review，检查工作树和 Git 历史里是否出现个人路径、邮箱、token、私有域名或大二进制文件。
