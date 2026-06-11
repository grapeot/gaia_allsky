# Gaia 全天星空实验

把 Gaia 卫星实测的恒星数据一颗颗画回天上。欧洲航天局的 Gaia DR3 星表记录了 18 亿颗恒星的位置、星等和颜色——这个项目取其中的七百多万颗较亮恒星，连同耶鲁亮星表补上的亮星，按真实物理数据渲染全天星图。银河没有单独画：星密到一定程度，密度分布自然会浮现出那条横跨天空的光带。正因为每一颗星的亮度和颜色都锚定在实测数据上，光污染、人眼灵敏度、观测位置这些影响观测的因素都可以做成物理旋钮，逐级调节。

GitHub Pages 放了压缩后的结果图、九级光污染的交互对比、飞行视频和十亿像素高清预览。这个 README 解释模型、复现方法和二次开发接口。

## 可以看到什么

三类输出，每类回答一个直觉问题。

**第一类：Bortle × 敏感度矩阵对比图。** 把光污染旋钮和人眼灵敏度旋钮交叉组合，用广州纬度的地平视角渲染银河，同时看到天空亮度和观测能力的组合效果。Bortle 1 顶级暗空的经验 NELM（裸眼极限星等）可到 7.6-8.0 等；常说的"肉眼 6 等"更接近普通暗天的标准，不是人眼的物理上限。

**第二类：Bortle 1 到 9 的银河消失序列。** 从暗空保护区到城市中心，九张图放在同一画面里，共享同一个亮度基准。很多爱好者有过这种体验：暗空里银河一眼可见，回到城市什么都看不到——这组图让你逐级看到中间发生了什么。

**第三类：星际飞行视频。** Gaia 测了距离，可以算出从任何位置看到的星空。朝北斗七星方向飞出去几十光年，勺子就散了架——七颗星里最近的 78 光年，最远的 124 光年，连成勺子只是从地球视角看过去的巧合。银河纹丝不动，它是几千光年尺度的结构。前向透视版看星座散架，VR 全景版可以转头看任意方向。

## 两种输出方式

同一条渲染管线可以落到两种成品形态，选哪一种取决于目标分辨率。

**单图模式**直接渲一张平面图，用广州地平 perspective 视角看银河（`src/render_bortle_eye_grid.py`、`src/render_fov.py`）。方便浏览、分享、发公众号。8K 以下建议用单图。

**HiPS 模式**面向 8K 以上直到十亿像素级别的超大渲染。这个尺度单张大图打不开也没法分享，改用天图查看器 Aladin Lite 浏览：渲染带 WCS 的 TAN（gnomonic）投影图，用 `java -jar AladinBeta.jar -hipsgen in=dir color=png` 拼成 HiPS 金字塔，可以 zoom in 看单颗恒星、zoom out 看乳光涌出的银河。

TAN 投影银心图的最小验证（单图）：

```bash
python src/render_tan_wcs.py \
  --data data/raw/fov_g20.npz --out outputs/tan_gc \
  --lc 0 --bc 0 --fov-deg 40 --size 1024
```

生成 `outputs/tan_gc.png` 和同名 `outputs/tan_gc.hhh`（FITS WCS header）。

实际出 HiPS 走**瓦片模式**（标准 HiPS 做法：多张小图 + 各自 WCS，比单张巨图快得多）。把整个 FOV 切成网格，每格一张小图，多进程并行渲（worker 数与 tile-size 不变则内存恒定，凑更高分辨率只需更多格）：

```bash
# 全 FOV 瓦片（fov=6/step=5/size=2048 ≈ 10 亿像素等效；放大调小 tile-fov/step）
python src/render_tan_wcs.py \
  --data data/raw/fov_g20.npz --out outputs/tiles --tiles \
  --l-range=-41,79 --b-range=-31,43 \
  --tile-fov 6 --tile-step 5 --tile-size 2048 --workers 8
```

再用 hipsgen 把瓦片拼成 HiPS 金字塔（需 `openjdk@11`，新版 JDK 不兼容旧 jar）。`color=jpeg` 输出比 PNG 小、便于分发，`target` 放 FOV 中心（银道 5,-2.5 → 赤道 271.672,-25.873），`fading=true` 羽化重叠区消接缝（瓦片重叠带恰好是各自 gnomonic 边缘畸变最大处，默认 mean 混合会留下亮处可见的接缝；fading 平滑过渡消除它，不要用 border 裁边，会留黑缝）：

```bash
java -Xmx80g -jar AladinBeta.jar -hipsgen \
  in=outputs/tiles out=outputs/hips color=jpeg \
  "target=271.672 -25.873" fading=true
```

10 亿像素版（fov=6 的 338 张瓦片）生成 Norder0-6 七层金字塔、约 1.3 万瓦片、1.2GB、约 16 分钟。HiPS 体量超出 GitHub Pages，部署时把瓦片目录放在自有服务器（如经 Cloudflare），页面里的 Aladin Lite 跨域加载时由 Cloudflare 加 `Access-Control-Allow-Origin` 头即可；8K 以下的单图直接嵌页面。

像素映射用 `+xi`（东向）配 WCS `CDELT1<0` 表达"经度向左增"，只处理一次手性；若两处都加负号，Aladin 里图会左右镜像。

亿级星表的单图渲染走并行入口 `src/render_fov.py`（详见下文"复现正式图片"）。

## 科学边界

这个项目追求定性正确和可解释，不追求测光级精确。物理模型部分按公开数据完成，视觉层是工程显示，不是光谱计量模拟。

已有物理锚点的部分：

- 星等到亮度：Pogson 公式 `L = 10^(-0.4 * (m - m_ref))`
- BP-RP 色指数经 Pecaut & Mamajek 主序星标定 → 表面温度 → 黑体谱积分 → sRGB，太阳 G2V（5772K）锚定中性白
- Gaia 银道坐标、赤道坐标和地平坐标投影
- Bortle 暗空等级到天空背景面亮度的相对梯度（Bortle 1 约 7.8 等，Bortle 6 约 5.3 等）
- 3D 视差重投影和平方反比亮度变化

显示层仍然需要 tone mapping。屏幕是 SDR，人眼暗视觉也不是线性相机。正式视觉图使用固定 sky floor、共享 reference stretch，以及一个模仿真实光学系统的星点成像模型：所有恒星共用同一个窄 PSF（点扩散函数，0.6 像素），G=11-13 暗星乘截断补偿增益代理 G>13 不可分辨恒星的积分光（增益由 Gaia 光度函数外推给出），超过饱和线的亮星能量按散射翼重新散布。银河乳光和尘埃暗带直接来自恒星计数的正负信息，不引入银河贴图或尘埃模型。显示参数在代码里显式暴露，不会混进物理模型——星星的物理亮度由公式决定，屏幕观感由显示参数控制，两套体系各自独立。

还有一个重要边界：Gaia 可见光视差只覆盖太阳附近一个有限数据球，不是整个银河盘面。因此本项目能演示星座散架、局部星场重投影和数据球边界，不能生成一张真正的银河俯视图。人类本来也没有那种照片；网上常见的"银河系全景图"是多波段观测加模型反演得到的想象图，不是任何相机拍出来的。

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

这两张图使用同一套 normalization 思路：每个 panel 单独适应 sky floor，整张 grid 共享一个显示 reference，暗空图不浪费 SDR 动态范围，高光污染 panel 也不会被各自拉亮。

星点累积使用统一 PSF 成像模型：`--psf-core-px 0.6` 对所有恒星相同（核取窄是因为亮星显大交给饱和溢出，核心 PSF 只负责给点源真实的成像形态），`--faint-gain 3.8` 补偿 G=13 星表截断（增益保总量但保不了质感，能用真实星就不用增益代理，见 docs/working.md），`--sat-over-sky 6` 控制亮星饱和溢出的起点。尘埃暗带由暗星计数的缺失直接呈现，不需要额外遮罩。`--ext-threshold 0.035` 是弥散光的 Weber 对比阈值：低于该比例 skyglow 的低频结构对人眼不可见，这让银河按真实观测经验在 Bortle 7 左右消失。

### 亿级深星表的并行单图渲染

增益代理截断后的不可分辨族群，是数据稀疏时的聪明做法；但数据量上来后，真实暗星会自然涌现出比近似更细腻的乳光和更有结构的暗带。广州 FOV 的深星表（约 6.16 亿颗 G<20 真实恒星）一颗颗画出来，比 G<13 加增益的版本明显更耐看：

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
