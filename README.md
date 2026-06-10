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

## 两种输出方式

同一条物理渲染管线可以落到两种成品形态，选哪一种取决于目标分辨率，而不是内容。

第一种是**单图模式**，直接渲一张平面图，用广州地平 perspective 视角看银河（`src/render_bortle_eye_grid.py`、`src/render_fov.py`）。它方便浏览、分享、发公众号，现有的正式对比图和飞行视频都走这条路径。8K 以下的规模建议都用单图：一张图就能完整看到，不需要额外的查看器。

第二种是 **HiPS 模式**，面向 8K 以上直到十亿像素级别的超大渲染。这个尺度的单张大图既打不开也没法分享，所以改用天图查看器 Aladin Lite 来浏览：渲染若干张带 WCS 的 TAN（gnomonic，球面切平面）投影图，用 `java -jar AladinBeta.jar -hipsgen in=dir color=png` 把它们拼成 HiPS 金字塔，Aladin Lite 加载金字塔后可以 zoom in 看锐利的单颗恒星、zoom out 看乳光涌现的银河，像在 DSS 巡天图上漫游一样。

判断标准很简单：**8K 以下用单图（建议），更大规模才上 Aladin HiPS。** HiPS 模式有一个单图模式不需要面对的关键技术点——立体角归一化。星光是 flux 语义，每像素亮度随像素角面积变化，不同投影和分辨率下同一颗星给出的每像素亮度并不相同（这正是 TAN 图看起来比广州地平图暗的真因）。`src/render_tan_wcs.py` 在累积后把每像素除以像素立体角，转成与分辨率无关的面亮度，让一套 tone 参数在所有层级通用。详见 `docs/rfc.md` 第四层和 `docs/working.md` 末尾几节。

TAN 投影银心图的最小验证：

```bash
python src/render_tan_wcs.py \
  --data data/raw/fov_g20.npz --out outputs/tan_gc \
  --lc 0 --bc 0 --fov-deg 40 --size 1024
```

生成 `outputs/tan_gc.png` 和同名 `outputs/tan_gc.hhh`（FITS WCS header）。把多张这样的 PNG+.hhh 放进一个目录，再用 hipsgen 拼金字塔（需 `openjdk@11`，新版 JDK 不兼容旧 jar）：

```bash
java -jar AladinBeta.jar -hipsgen in=outputs/tan_tiles color=png out=outputs/hips
```

亿级星表的单图渲染走并行入口 `src/render_fov.py`（详见下文"复现正式图片"）。

## 科学边界

这个项目追求的是定性正确和可解释，不追求测光级精确。物理模型部分已经按公开数据完成，视觉层仍然是工程显示，不是对人眼的光谱计量模拟。

已经有物理锚点的部分：

- 星等到亮度：`L = 10^(-0.4 * (m - m_ref))`
- BP-RP/B-V 简化色指数到恒星颜色：蓝白星、太阳型星、橙红星有可见差异
- Gaia 银道坐标、赤道坐标和地平坐标投影
- Bortle 暗空等级到天空背景面亮度的相对梯度
- 3D 视差重投影和平方反比亮度变化
- 经验 Bortle/NELM 表：Bortle 1 约 7.8，Bortle 6 约 5.3

显示层仍然需要 tone mapping（亮度映射）。屏幕是 SDR（标准动态范围），人眼暗视觉也不是线性相机。正式视觉图使用固定 sky floor、共享 reference stretch，以及一个模仿真实光学系统的星点成像模型：所有恒星共享同一个小 PSF（点扩散函数），G=11-13 暗星乘一个截断补偿增益代理 G>13 不可分辨恒星的积分光（增益由 Gaia 光度函数外推给出），超过饱和线的亮星能量按散射翼重新散布。银河乳光和尘埃暗带都直接来自恒星计数的正负信息，不引入额外的银河贴图或尘埃模型。这些显示参数在代码里显式暴露，不会混进物理模型。换句话说：星星的物理亮度由公式决定，屏幕观感由显示参数控制，两套体系各自独立。

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

仓库不会提交 Gaia 原始缓存和渲染输出。`data/raw/` 和 `outputs/` 都被 `.gitignore` 排除。星表文件、Flatiron bulk mirror、广州银心 FOV 原始 gzip 和本地过滤约定见 `docs/gaia_catalog_usage.md`。

3D 飞行所需的近邻恒星子集可用：

```bash
python src/fetch_gaia_3d.py
```

正式渲染使用全天 G<13 星表（约 740 万颗）。Gaia 档案库对匿名查询有 300 万行硬上限，超限会被无声截断，所以暗星段必须分星等区间获取再合并：

```bash
python src/fetch_gaia_allsky.py --gmax 11 --output data/raw/gaia_g11.npz
python src/fetch_gaia_allsky.py --gmin 11 --gmax 12 --output data/raw/gaia_g11_12.npz
python src/fetch_gaia_allsky.py --gmin 12 --gmax 12.5 --output data/raw/gaia_g12_125.npz
python src/fetch_gaia_allsky.py --gmin 12.5 --gmax 12.8 --output data/raw/gaia_g125_128.npz
python src/fetch_gaia_allsky.py --gmin 12.8 --gmax 13.0 --output data/raw/gaia_g128_13.npz
python - <<'PY'
import numpy as np
parts = ["data/raw/gaia_g11.npz", "data/raw/gaia_g11_12.npz", "data/raw/gaia_g12_125.npz",
         "data/raw/gaia_g125_128.npz", "data/raw/gaia_g128_13.npz"]
arrs = {k: np.concatenate([np.load(p)[k] for p in parts]) for k in ["l", "b", "g", "bp_rp"]}
np.savez("data/raw/gaia_g13.npz", **arrs)
PY
```

合并前检查每段行数：任何一段恰好 3,000,000 行就说明被截断了，需要进一步细分区间重取。

输出文件字段包括：

```text
l, b, g, bp_rp
```

其中 `l/b` 是银道坐标，`g` 是 Gaia G 星等，`bp_rp` 用于近似恒星颜色。合并后的 G<13 缓存约 740 万颗星（225MB），属于本地数据文件，不进入 Git 历史。只想轻量体验的话，单独用 G<11 缓存（约 125 万颗）配 `--faint-mag-min 9 --faint-gain 4.2` 也能跑。

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
星点累积使用统一 PSF 成像模型：`--psf-core-px 0.6` 对所有恒星相同（核取窄是因为亮星显大交给饱和溢出，核心 PSF 只负责给点源一点真实成像形态，过宽会把暗星颗粒糊平），`--faint-gain 3.8` 补偿 G=13 星表截断（增益保总量、保不了质感：G11→G13 的对比验证了能用真实星就不要用增益代理，见 docs/working.md），`--sat-over-sky 6` 控制亮星饱和溢出的起点。尘埃暗带由暗星计数的缺失直接呈现，不需要额外遮罩。`--ext-threshold 0.035` 是弥散光的 Weber 对比阈值：低于该比例 skyglow 的低频结构对人眼不可见，这让银河按真实观测经验在 Bortle 7 左右消失，而不是在画面里一直保留微弱残影。

### 亿级深星表的并行单图渲染

增益代理截断后的不可分辨族群，是数据稀疏时的聪明做法；但数据量上来后，真实暗星会自然涌现出比近似更细腻的乳光和更有结构的暗带。把广州 FOV 的深星表（约 6.16 亿颗 G<20 真实恒星）一颗颗画出来，比 G<13 加增益的版本明显更耐看。这条路径的两个脚本：

```bash
# 1) 并行解压 Flatiron 分片，build 覆盖整个 FOV 的深星表缓存
python src/build_fov_deep_cache.py --gmax 20 --out data/raw/fov_g20.npz --workers 16

# 2) 并行渲染（深星表已含真实暗星，--faint-gain 1.0 关闭增益代理）
python src/render_fov.py \
  --data data/raw/fov_g20.npz --out outputs/fov_g20.png \
  --faint-gain 1.0 --workers 28 --save-linear outputs/fov_g20_linear.npy
```

6 亿星的逐星坐标变换是内存和时间瓶颈，所以 `render_fov.py` 把"逐星处理→累加到线性画布"这一段分块并行（28 worker），PSF 卷积和整条非线性显示链留在主进程对合并后的画布做一次，数值与单进程逐像素一致。`--save-linear` 把显示链之前的线性画布存成 `.npy`，之后专调 tone mapping 用 `src/tone_iterate.py` 读它秒级迭代，不必重渲 6 亿星。

这条路径要求本地有 Flatiron 深星表分片，获取约定见 `docs/data_manifest.md` 和 `docs/gaia_catalog_usage.md`。

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

两个视频都会先并行渲染 PNG 帧，再用 ffmpeg 合成 SDR H.265 mp4（libx265 + hvc1 tag，Safari/Chrome 均可直接播放；`--codec libx264` 可回退）。帧目录默认保留，便于检查和重新编码。
视频与静态图共用同一套统一 PSF 成像模型（`--psf-core-px 1.1 --faint-gain 4.3`，3D 星表 G<13）。太空视角没有 skyglow，亮星饱和溢出改锚到固定参考视星等：`--sat-ref-mag 6.0 --sat-over-ref 6.0`，整段视频饱和起点恒定，不随观测者移动逐帧抖动。旧的 `--bloom-strength`/`--bloom-sigma` 参数已移除。

## 代码结构

```text
src/
  render_starmap.py            星等、星色、全天投影、星点累积、SDR/HDR tone map
  render_horizon.py            地平坐标和 Bortle skyglow 模型
  render_bortle_eye_grid.py    Bortle × NELM 视觉对比图（核心显示链所在）
  render_3d.py                 Gaia 视差 3D 重投影
  motion.py                    共享 L 型飞行轨迹
  video_common.py              并行逐帧渲染、PNG/TIFF 帧、ffmpeg 合成
  render_vr_video.py           VR equirectangular 视频入口
  render_big_dipper_video.py   前向透视视频入口
  fetch_gaia_allsky.py         Gaia 全天 G 星等缓存获取
  fetch_gaia_3d.py             Gaia 近邻 3D 子集获取

  # 单图模式（亿级深星表）
  build_fov_deep_cache.py      并行解压 Flatiron 分片 → FOV 深星表缓存
  render_fov.py                亿级星表的并行单图渲染入口
  tone_iterate.py              在已存的线性画布上秒级迭代 tone mapping

  # HiPS 模式
  render_tan_wcs.py            TAN(gnomonic) 投影 + 立体角归一化 + WCS 输出，喂 hipsgen

  # 研究/诊断脚本（非正式产物路径）
  probe_rift_depth.py          大裂隙对比 vs 星表深度的真数据 probing
  probe_rift_windows.py        裂隙/亮云窗口的 HEALPix 分片定位
tests/
  test_render.py               物理、投影、运动、tone map 和 CLI 语义测试
docs/
  prd.md                       科学目标和成功标准
  rfc.md                       实现设计和边界（含两种输出方式的分层归属）
  test.md                      测试策略
  bortle_skyglow.md            Bortle/SQM/NELM 参考表
  working.md                   历史决策和调参记录（含超大图金字塔双 bug、立体角归一化）
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
