# 实现设计

## 管线分层的动机

把星表查询、坐标投影、显示调参全写进同一个循环，前几次迭代很快，但后续每增加一种投影、每换一个 Bortle 参数、每出一个新输出格式，都要在同一个文件里到处改。改显示参数的人只需要面对第三层，改投影的人只需要面对第二层。各自处理的都是 numpy 数组和明确的坐标与亮度值。

管线切成四层，每层只做一件事，层与层之间通过显式的数值传递：

1. **星表数据** — 从 Gaia DR3 拿到恒星的基本物理量（位置、亮度、颜色）。
2. **物理投影** — 把物理量变成屏幕上的像素位置和线性亮度。
3. **显示映射** — 把线性亮度压到 SDR 屏幕能显示的范围。
4. **输出包装** — 把渲染结果写成 PNG / TIFF / MP4，生成 Web 预览资产。

换一种星表（比如 Hipparcos）只需要重写第一层；换一种投影只需要改第二层；想用 HDR 输出只需要改第三层。每层之间的接口由 numpy 数组和明确的坐标与亮度值构成，不传参数名，不传配置对象。每个脚本可以独立 debug，不需要理解整条管线。

## 第一层：星表数据

这是唯一涉及外部科学数据的层。两个脚本分别应对两种观测场景：

- `src/fetch_gaia_allsky.py` — 从 Gaia DR3 查询全天恒星，输出银道坐标 `(l, b)`、G 星等和 BP-RP 颜色。这是静态全天图的数据基础。
- `src/fetch_gaia_3d.py` — 查询恒星的三维信息：RA / Dec、视差（parallax）和 G 星等。视差反推距离后用于 3D 飞行。

一层设计的关键 trade-off：星表查询和后续渲染完全解耦。查询结果的缓存是独立的 `.npz` 文件，渲染脚本只读不写这些文件。可以离线跑一次慢的 Gaia ADQL 查询，然后无数次用不同参数跑渲染，每轮渲染耗时相同，不重新查数据库。代价是 `data/raw/` 不进 Git——它是外部数据的本地拷贝，不是源码。

## 第二层：物理投影

物理投影把恒星的物理坐标和物理亮度映射到屏幕坐标系，不涉及任何显示美学。

- **静态全天图**：支持 equirectangular（等距圆柱投影，用于 VR 视频）和 Mollweide（用于传统全天地图展示）。
- **地面视角**：先把银道坐标 `(l, b)` 转为赤道坐标（RA/Dec），再按观测纬度（`lat_deg`）和地方恒星时（`lst_hours`）转为地平坐标（方位角/高度角），最后从地平坐标投影到相机视场。
- **飞行视频**：先把恒星视差转为三维笛卡尔坐标（视差→距离→ `(x, y, z)`），然后从移动观测者位置重新投影这些三维点到相机平面。

投影是几何运算，tone mapping 是感知运算，两者分开处理。如果有人想用同样的星表数据做光谱分析或星等统计，直接读第二层的线性亮度输出，完全不受 gamma、PSF、skyglow 这些参数的干扰。

## 第三层：显示映射（tone mapping）

这是整个管线中工程决策最密集的一层，核心问题是：Gaia 恒星的线性光通量跨度超过 10^6，而 SDR 屏幕对比度只有几百，必须把天文亮度动态范围压缩到屏幕范围内，同时保持可读的视觉层次。

处理步骤（由 `render_bortle_eye_grid.py` 和 `render_starmap.py` 实现）：

1. **统一 PSF 累积**：所有恒星共享一个小高斯 PSF（`psf_core_px=1.1`），G≥9 暗星乘截断补偿增益（`faint_gain=4.2`，见下节）。
2. **饱和溢出**：线性亮度超过 `sat_over_sky × skyglow` 的能量被截下，按宽高斯溢出翼重新散布（见下节）。
3. **弥散光 Weber 阈值**：低频分量低于 `ext_threshold × skyglow` 的部分对人眼不可见，从画面中移除（见下节）。
4. **估计 sky floor**：对每个 panel，取低百分位（`sky_pct=25.0`）的亮度作为天空背景估计。
5. **锚定 sky floor**：把每个 panel 的 sky floor 映射到同一个固定深灰值（`target_sky=0.03`）。
6. **增强信号对比**：sky floor 以上的信号乘以 `star_contrast=4.0`，让恒星光点从背景中分离。
7. **shared stretch**：选一个 reference panel，计算一个 white percentile（`white_pct=99.5`）到 `target_white` 的统一映射。
8. **统一 gamma 输出**：所有 panel 用同一个 signal stretch，再做 `gamma=2.2` 输出。

第七步 shared stretch 的核心效果：如果每个 Bortle 等级的 panel 独立做 normalization，Bortle 7-9 的 panel 会被强行拉亮，读者会误以为光污染下也能看到差不多的星光，这与真实夜空观测经验相反。统一 stretch 保证了高光污染 panel 真正变暗，读者看到的是客观的对比，而不是被独立归一化补救过的假夜空。

所有参数——`psf_core_px`、`faint_gain`、`sat_over_sky`、`star_contrast`、`target_sky`、`sky_pct`、`white_pct`、`target_white`、`gamma`——都在 CLI 参数里显式暴露。这是刻意的设计：任何一个参数的含义和影响都是透明的，不会被藏在物理模型里假装是天体物理常数。

## 第四层：输出包装

渲染结果被写成 PNG / TIFF / MP4，同时为 GitHub Pages 生成压缩预览（JPEG / WebP / 小尺寸 MP4）。

不同发布渠道对格式、分辨率、文件大小的要求不同。GitHub Pages 需要 WebP 和低分辨率 JPEG；开发者在本地想用 TIFF 做灰阶检查；VR 视频需要 equirectangular 格式。输出层不改变渲染结果的内容，只改变容器格式。

## 星等与颜色

星等转线性亮度的基本公式是 Pogson 公式：

```text
L = 10 ^ (-0.4 * (m - m_ref))
```

这是对数换底——差 5 等相当于亮度差 100 倍。`m_ref` 是人为选定的参考星等，它决定整个系统的亮度 anchor。`m_ref` 是显示层参数而不是物理常数，因为 Pogson 公式本身是比例关系，参考点的选取不影响恒星之间的相对亮度比值。

颜色方面，恒星温度通过 BP-RP 或 B-V 色指数映射到 RGB。人眼在暗光下对色彩的敏感度远低于星际介质的消光精度，这个映射不需要追求精确的光谱色彩还原。它只需要让蓝白星和橙红星在大尺度全天图里产生可见的颜色差异。这已足够让银河从星表里自然出现：银盘带里的颜色梯度来自真实恒星的温度分布。

## Bortle 暗空等级和 NELM

Bortle 等级是一种主观的暗空质量分类，从 1（最暗）到 9（城市中心）。代码里用了两条独立的经验映射：一条把 Bortle 映射到天空面亮度，再换算成 skyglow；另一条把 Bortle 映射到 NELM（裸眼极限星等，Naked-Eye Limiting Magnitude）。

NELM 表如下：

| Bortle | NELM |
|--------|------|
| 1 | 7.8 |
| 2 | 7.3 |
| 3 | 6.8 |
| 4 | 6.3 |
| 5 | 5.8 |
| 6 | 5.3 |
| 7 | 4.8 |
| 8 | 4.3 |
| 9 | 4.0 |

skyglow 来自 `render_horizon.py` 中的 Bortle 面亮度表，不由 NELM 反推。skyglow 是一个加性的线性背景，叠加在恒星信号之上：

```text
observed = stars + skyglow
```

skyglow 越大，星光对比度越低，直到暗星被淹没。这个加性模型解释了为什么 Bortle 9 的城市只能看到几十颗最亮的星，而 Bortle 1 的暗空能看到银河结构。

代码中，有效极限星等由 `+delta_mag` 参数控制：对于给定 Bortle，`effective_nelm = BORTLE_NELM[bortle] + delta_mag`。正 delta 表示通过望远镜或长曝光提高了极限星等；`+2mag` 意味着比当前 Bortle baseline 高 2 等。处在有效极限星等位置的恒星被渲染为 skyglow 的 `limiting_contrast=0.5` 倍——这个对比度直接锚定在 skyglow 上，对应极限星等位置恒星的可见度阈值。

## Bortle Eye Grid 的 tone mapping 算法

`render_bortle_eye_grid.py` 的正式视觉模式是整个项目的核心可视化产物。它在一个网格里展示多个 Bortle × delta_mag 组合，每个 panel 是给定观测条件下地平坐标视角的夜空。

shared reference stretch 的具体做法（对应 `render_grid` 函数中的逻辑）：

1. 为每个 (Bortle, delta_mag) 组合独立渲染一次线性 canvas。渲染包括：坐标投影 → 星等转亮度（锚定在 NELM 上）→ 叠加 skyglow。
2. 选一个 reference panel 作为亮度锚点。默认用所有 panel 中 white percentile 最亮的那个（`--reference-mode brightest`）；也可以显式指定 `--reference-bortle` 和 `--reference-value`（例如 Bortle 1 / +2mag）。
3. 对 reference panel 做 `adapt_sky_floor`（sky floor → target_sky，然后 boost signal），得到参考信号范围。
4. 根据 reference panel 的信号范围计算单一个 `signal_stretch`，使得 white percentile 映射到 `target_white`。
5. 所有 panel 都用这个相同的 `signal_stretch` 做 gamma 输出。

shared stretch 的设计理由：手调每个 panel 的亮度对比度会把整个网格变成一个展示者决定的视图，而不是一个客观的对比视图。shared stretch 把自由度限制在一个维度上——选 reference 决定了整体对比尺度，但每个 panel 的相对亮度关系由物理层决定，不能被单独修亮。

## 统一 PSF 与饱和溢出

真实光学系统里，PSF 形状对所有恒星严格相同：它是光学系统的属性，不是恒星的属性。亮星看起来更大来自两个机制：tone curve 饱和让同一高斯轮廓更宽的部分越过显示阈值，以及散射翼（镜筒散射、衍射，对应 Moffat 轮廓的幂律尾巴）把截断的能量摊到更大范围。正式视觉模式按这个成像模型实现星点累积（`accumulate_uniform_psf_stars`）：

1. **统一核心 PSF**：所有恒星共享 `psf_core_px=1.1` 的高斯核。实现上 PSF 是对整幅 canvas 的一次卷积，代价与星数无关，几十万颗暗星和几千颗亮星的处理成本相同。
2. **饱和溢出**（`saturate_and_bloom`）：卷积后，线性亮度超过饱和线的像素被截到饱和线，截下的能量按双高斯溢出翼（`wing_sigmas=3,9`，`wing_weights=0.65,0.35`）重新散布。能量守恒：溢出翼只是把过亮核心的光摊开，不引入额外亮度。饱和线 = `sat_over_sky=6.0` × skyglow × 10^(0.4·delta_mag)：`+delta_mag` 把全部星光乘 10^(0.4·delta)，饱和线必须乘同一个系数，让饱和起点固定在距有效极限星等固定的星等深度上。如果把饱和线锚死在固定倍数的 skyglow 上，+4mag 面板会有约 25% 的像素被截断、37% 的星光摊进糊翼，整条银河被洗成一片柔光。

这个结构保证恒星视尺寸随亮度连续增大：暗星是 1px 颗粒，中等星是小圆点，亮星是带散射晕的饱和盘面，之间没有任何分段缝。历史方案的问题都出在破坏这个单调性上——双层 PSF（每颗星同时画一个锐点和一个 6px 糊斑）让亮星读起来像撒在磨砂底上的噪点；分段增益方案（seg_medium 实验）给暗星比亮星更宽的 PSF，尺寸关系直接反转。

## 暗星截断补偿（faint gain）

肉眼看到的银河乳光是 G=11 以下几十亿颗不可分辨恒星的积分光，而星表在 G=11 截断。补偿方法：G≥`faint_mag_min=9.0` 的暗星亮度乘 `faint_gain=4.2`，让 G=9-11 暗星代理整个未分辨族群。

这个增益有数据依据：用本仓库 Gaia 缓存拟合光度函数，G 8-11 的 dlogN/dm 斜率约 0.412，意味着每个星等 bin 的积分光通量几乎相等；外推到 G=21，缺失的积分光约为 G9-11 bin 的 5.8 倍，对应合理增益区间 4-7。`faint_gain=4.2` 取区间下半部，因为光度函数斜率在更暗端实际会变缓。

G=9-11 暗星与未分辨族群跟踪同一个空间分布，所以这一步同时解决了尘埃暗带问题：哪里缺少暗星，哪里的增益光就同步缺失，暗带由星数的负信息直接呈现，不需要额外的密度遮罩、尘埃 map 或银河贴图。（旧版曾用一个 G9-11 星数低频遮罩压暗缺星区，作为宽 PSF 填平暗带的补救；统一 PSF 模型下不再需要。）

## 弥散光 Weber 阈值

人眼对点源和对大面积弥散光的探测阈值不同：低于天空背景百分之几的弥散结构，人眼看不见，相机长曝光却拍得到。这正是银河在 Bortle 7 左右从视觉中消失的机制——按本仓库的测量，银河带低频信号相对 skyglow 的对比度从 Bortle 1 的约 93% 一路降到 Bortle 9 的约 2.8%，而经典 Weber 对比阈值就在百分之几这个量级。如果显示层把任何正对比都放大显示，Bortle 9 的画面里还能隐约看到银河，与真实观测经验相悖。

`apply_extended_visibility_threshold()` 按空间频率区分两类光：用 `ext_sigma=8.0` 像素的高斯把星点 canvas 分成低频（弥散光）和高频（点源）两部分，低频部分减去 `ext_threshold=0.035` 倍 skyglow（减到零为止），高频部分原样保留。点源的可见度仍由 NELM 锚定，不受影响；弥散银河带则在对比度低于阈值的天空下整体消失。

两个锚定细节。第一，阈值锚定在 skyglow 上，而且刻意不随 `+delta_mag` 灵敏度增益缩放：模型里灵敏度提升表现为星光相对天空的对比度按 10^(0.4·delta) 放大，放大后的弥散对比一旦越过阈值，银河就重新可见——这正是光学辅助让城市里也能看到银河的机制。第二，阈值施加在饱和溢出之后，亮星散射翼是人眼实际感知到的眩光，强度远超阈值，不受影响。

默认 0.035 的标定依据：施加后 Bortle 7 的带对比从 5.9% 降到 2.5%（约等于旧渲染的 Bortle 9），Bortle 8-9 降到 1% 以下基本不可见，与 Bortle 分级的原始描述（7 级银河完全不可见，6 级仅天顶可辨）对齐；Bortle 1 仅从 93% 降到 89%，暗空几乎无感。

## Bortle scale reference（Bortle 1-9 对比图）

Bortle 1-9 对比图用同一套代码渲染，通过参数区分：

```bash
--reference-bortle 1 --reference-value 2
```

这组参数的含义：用 Bortle 1 / +2mag 这个最佳可读 panel 来校准整张 scale grid 的显示 stretch。Bortle 7-9 的 panel 在同一 stretch 下自然变暗，让读者直观感受到光污染对可见星空的压制。Bortle 1 的夜空在物理层并没有被调亮——reference 只改变了显示层的亮度映射，物理星等和 skyglow 数值保持不变。

不同的 reference 参数产生不同对比度的图，都基于同一组物理输入。觉得 Bortle 1 / +2mag 太亮、Bortle 9 一片黑时，换 reference 即可获得不同的对比度分布。但物理层的数值没有被改动——不能因为显示效果不理想就说计算有误。

## 飞行视频

项目包含两类飞行视频，共享同一条空间轨迹：

- **VR 版**（equirectangular 输出）：用于 VR 头显，观测者可以在任意方向转头。
- **Forward 版**（透视相机输出）：标准平面视频，在画面上绘制北斗连线帮助观察星座形状如何随位移而变化。

轨迹设计：先朝北斗方向做短距离飞行，验证走近了星座散架的视觉效果；再转向银心方向上方飞行，展示银河系盘面的三维深度和 Gaia 数据球边界。

所有视频先渲染 PNG 帧再用 ffmpeg 合成。中间 PNG 帧不进 Git，但保留本地不删。保留中间帧的目的有两个。第一，可以逐帧检查渲染质量和投影正确性。第二，将来需要换编码格式（H.264 → H.265 → AV1）时，不需要重新跑整个渲染，直接换 ffmpeg 参数重新编码。

## 数据边界

Gaia DR3 的视差数据覆盖范围有限——可见光波段的视差精度随距离衰减，高质量的距离数据只覆盖太阳附近的局部数据球，远不到覆盖整个银河系盘面所需的距离。当飞行视频的观测者位置飞出这个数据球，视野中的恒星消失，看到的是数据球的边界。

这是 Gaia DR3 视差精度的物理限制。代码和文档必须明确保留这个边界，不能通过数据插值或外推来假装有个全盘视图。如果将来有其他星表（例如 Gaia DR5 或 LSST）覆盖了更大范围，换数据源只需要改第一层，其他三层不变，这正是分层设计希望达到的效果。

## 公开发布策略

Git 仓库只放代码、文档、测试和小型网页资产（如 GitHub Pages 的压缩预览）。大体积数据不进 Git：

- **不进 Git**：`data/raw/` 中的大 `.npz` 缓存、完整渲染帧 PNG、完整视频 MP4。
- **进 Git**：`src/`、`tests/`、`docs/`、`README.md`、`requirements.txt`、压缩后的 GitHub Pages 资产。

发布时，GitHub Pages 使用压缩 JPEG / WebP / MP4 作为展示预览，并提供可选的原图下载链接（从外部存储或 release assets 获取，不存仓库内）。

这个策略的 trade-off：新 clone 仓库的人不能直接跑 `python src/render_bortle_eye_grid.py`（缺少 `data/raw/gaia_g11.npz`），需要先跑一次 `python src/fetch_gaia_allsky.py --gmax 11 --output data/raw/gaia_g11.npz` 下载星表。换来的好处是仓库维持在较小体积，方便在 GitHub 上正常协作和 CI。
