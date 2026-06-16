# 工作记录

## Changelog

### 2026-06-16（hipsgen N7 INDEX 卡住：PNG 缺 sidecar WCS）

全天 `allsky_po --hipsgen-only` 卡在 `Norder7 INDEX` 约一天。进程仍在刷新 log、单核满载，但 `o7/hips` 最近没有文件变化，`*.jpg` 为 0。`jcmd Thread.print` 显示主线程停在 `cds.hipsgen.BuilderIndex.scan -> Fits.loadHeaderFITS -> MyInputStream.fastExplorePNG -> RandomAccessFile.read`，说明它还在单线程扫描输入 PNG/header，未进入 `TILES`。`lsof` 定位到当前文件 `outputs/allsky_po/o7/tiles/tile_i0095_j0058_l174.07_b16.27.png`；统计发现 `o7/tiles` 共有 19503 个 PNG、19502 个 `.hhh`，唯一缺失 sidecar 的正是这个 PNG。

根因是 `render_tan_wcs.py --resume` 只按 `.png` 是否存在决定跳过，而 `render_tile` 先写 `.png` 再写 `.hhh`。worker 若在两步之间中断，后续 resume 会把半成品误判为完整 tile，留下有图无 WCS 的坏输入。HipsGen 对无 `.hhh` 的 PNG 会尝试从 PNG 内部探测校准信息，进入极慢/卡住路径；`maxThread` 对 `INDEX` 扫描无效，只影响后续 `TILES`。

修复：PNG 模式改为原子提交 `.png + .hhh` 成对产物；先写临时 PNG/HHH，再先 rename HHH、后 rename PNG。`--resume` 现在只有在 `.png` 和 `.hhh` 都存在时才跳过，否则重渲该 tile。这样即使中断，最终态也会被下一次 resume 识别为未完成并自动补齐。

恢复操作上，`o3`-`o6` 已经完成时可以只重跑高层：`bash tools/render_per_order_pipeline.sh allsky_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz --range 0,360 -90,90 "7 8" --workers 30 --hipsgen-par 1 --hipsgen-th 30 --step-frac 1.0 --hipsgen-only`。该命令完成后，再用 `bash tools/render_per_order_pipeline.sh allsky_po data/raw/gaia_allsky_g20_bsc5_hpx6.npz --range 0,360 -90,90 "3 4 5 6 7 8" --assemble-only` 把已有低层和新高层重新组装进最终 `outputs/allsky_po/hips`，不再跑 Python 渲染或 Java `hipsgen`。

### 2026-06-14（Allsky 预览纯黑 cell 边界缝）

Norder3 源 TAN tile 接缝通过 `STEPFRAC=0.8` 与 hipsgen `fading=true` 修掉后，用户继续在 zoom-out / Allsky 层看到大量细线。重新量化后区分了两类问题：第一类是 tile 边界附近亮度略低，不是主要观感问题；第二类是 `RGB=(0,0,0)` 的硬黑像素线，常沿 HEALPix cell / HiPS tile 边界走，Aladin 把 `Allsky.jpg` 投影到全天视图时会把 1px 黑缝插值成弯曲的 1-3px 黑线。`gz2_n3_overlap` 的高分辨率 Allsky 中仍有约 5,800 个被非黑像素夹住的 1px 纯黑像素，放宽到 2-4px 后是一万级。

根因在 Allsky 预览层，不是正式 Norder tile 内容：`rebuild_allsky_hires.py` 把 Norder3 JPEG cell 直接缩放并拼成 Allsky，partial-sky 覆盖边缘和重投影无采样处会以纯黑哨兵色进入预览图；这些小型内部黑连通块和 cell 边界黑带在单 tile 视图里不一定显眼，但 Allsky zoom-out 会放大成明显黑缝。修复限定在 Allsky 重建：只补被有效像素左右或上下夹住的纯黑窄缝，并补掉不接触图像外边界的小型纯黑连通块；只有一侧有内容的覆盖边缘保持黑，不向覆盖范围外扩张。这样只影响 `Norder3/Allsky.jpg` 预览，不改正式 HiPS Norder tile。

### 2026-06-14（direct HiPS 实验归档）

为了绕过 hipsgen 对 TAN 源图的 backward gather 重投影瓶颈，尝试过 direct HiPS：从点源星表直接 forward splat 到最终 HEALPix/HiPS tile。实验验证了若干有价值的不变量：HEALPix tile 参数平面等面积但不保角，文件内椭圆 PSF 在 Aladin 显示后可能是正确的；需要 `cdshealpix` 连续 `dx/dy`、局部 shear 补偿、subpixel bilinear splat、低 order tile guard band、以及 off-tile PSF wing/同 face 坐标一致性保护。

实验也暴露出当前实现的边界：N5 右边缘 seam 在邻 tile 齐全、同 face、off-tile wing 和连续坐标修复后仍未消失；放大后的块状斜向 PSF 更像 direct forward splat + undersampled elliptical kernel 的采样伪影，而不是单纯缺 tile 或亮星椭圆问题。局部继续调 kernel/block 参数收益不高。

结论：生产路径继续使用 TAN/WCS + hipsgen。direct HiPS 暂停并归档到 `docs/direct_hips_experiment.md`，未来若重启，应先做人工星表 + 单个 N5 邻 tile pair + global HEALPix child-grid / backward-gather reference 的最小复现实验，再考虑重写 direct renderer。

### 2026-06-14（跨机 benchmark：EPYC 128核 为何不比 M3 Ultra 32核 快）

**问题**：同一 per-order pipeline，EPYC 7763（128 逻辑核/64 物理/768G DDR4）感觉比 M3 Ultra
（32 核/512G 统一内存）慢，最初印象「慢 10×」，诧异 128 核反而输。

**最终结论（被所有数据支持、解释了最强约束）**：**EPYC 单核整体比 M3 Ultra 慢 ~2-2.6×，
就是代际+架构差异，没有隐藏的并行 bug。** 最强证据是 **Java(hipsgen) 和 Python(render) 两个
无关运行时都慢 ~2×**——这排除了任何 numpy/fork/Python 特有原因，只能是机器层面单核+内存
延迟整体慢 ~2×（M3 Ultra 2024/3nm/800GB·s vs EPYC 7763 2021/Zen3/DDR4）。最初的「10×」是
早期测量被多重干扰污染的假象（126 worker 严重过度订阅 + 噪声）；修干净后真实差距 ~2.6×。
128 核拉不回是因为：render 并行有调度退化、hipsgen 重投影本就单核 bound。

**硬数据**（心宿二/广州 tile，单 tile l=19 b=6 fov=5.4968 size=768）：
- 单 tile 单进程：EPYC ~870ms（稳态，连渲 8 次 922/875/864/872…）vs Mac ~330ms。慢 2.6×。
- hipsgen 重投影：EPYC ~Mac 的 1/2（用户实测）。← 与 render 同趋势、不同运行时。
- fork worker vs 主进程连渲：**完全一样**（922/875/864 vs 1047/869/866）→ 无 COW 缺页税。
- hipsgen 是端到端大头：Mac render_total 1132s，但 hipsgen N3-N7 已 1457s（N8 未计）。

**先后否决的 6 个错误假设（别再走的弯路，每个都被一条数据干净反驳）**：
1. **过度订阅（126w 抢 64 物理核）**：是早期噪声主因之一，但非根本——降到 64/32/16 端到端
   只从 73→68s 几乎不变。
2. **NUMA 跨 node**：EPYC 配成单 NUMA node（lscpu NUMA=1），不存在跨 node。
3. **HT 伪并行**：若是 HT 该到 ~64 物理核才崩；实测 W=16 就饱和，矛盾。用户反驳正确。
4. **内存带宽**：若带宽 bound，核应 stall→CPU idle；但 worker 实测 80% busy。用户反驳正确。
5. **并发写盘（目录锁）**：save vs nosave throughput 几乎一样（1.6 vs 1.5）→ 写盘无关。
   outputs 在 NVMe（nvme0n1p1 28T），单张 PNG 172ms。
6. **fork COW 缺页 / 页表锁争用**：最顽固的假设，但 fork worker 连渲 = 主进程连渲（无额外
   缺页税）干净否决。
   另外「主进程预处理 bv_to_rgb+visual_luminance 17s×6 层」是真实存在的串行成本，但它在
   tqdm 计时**之前**、不影响 render throughput（用户正确指出）；它影响端到端 wall-clock。

**方法论教训（这一轮踩坑最多）**：
- 反复用**不可比的基线**自我误导：拿「星少区 42ms tile」当基线推出假的「慢 20×」，换可比的
  同坐标 tile 后真相是「慢 2.6×」。跨机/跨配置比较，必须逐字相同的 tile/参数。
- tqdm 量的是 render 段、不含预处理——把预处理当 throughput 元凶错了好几次。分清「端到端
  wall-clock」vs「tqdm render throughput」，它们的瓶颈不同（前者 hipsgen+预处理大，后者纯计算）。
- 「CPU 占用率」是区分 compute-bound vs memory/IO-bound 的关键信号：busy→在算、idle→在等。
- 多假设要逐个用一条决定性数据**否决或确认**，不要堆叠未验证的假设。本轮 6 个假设全错，
  最后剩下最朴素的「单核代际差」反而对——奥卡姆。

**实操结论（写进 benchmark 工具默认）**：
- EPYC worker 用 ~物理核数（64），不要 126（HT 区过度订阅退化）。
- pipeline 已加 PROFILE 行（每层 render tiles/elapsed/throughput + hipsgen cells/elapsed +
  各 total），stdout/stderr 分流（tqdm 走 stderr、PROFILE 走 stdout），两机 tee 存 log 可比。
- 真要 EPYC 追上 Mac：单核代际差无解（换机器）；可减每 tile 内存搬运（float32 代 float64、
  gnomonic 先粗筛再投影）压常数，但拉不平 2× 代际差。

### 2026-06-14（zoom-out 结构丢失的最终诊断 + per-order 渲染落地）

承接 2026-06-10「两个致命 bug」那段（见下文「超大图 / Aladin HiPS 金字塔的两个致命 bug」）——
那次已诊断出"点源 flux 语义 + mean 池化稀释"，并写下正确架构「渲最高分辨率 float → sum 池化逐层
→ tone 每层独立」，但标注**待实现**，之后的 PR 一路仍走 hipsgen 的 mean/median 池化。这次把它真正
落地，并补上那段没有的一个新洞察。

**现象（用户报，三张图共诊）**：新版 bloom（只亮星给翼、暗星锐点）渲的全天图，zoom-out 看
一片黑 + 零散亮星，参考效果（老版大 PSF 高分辨率，糊但结构在）里那种"几亿暗星织成的发光底 +
尘埃暗带"完全消失。放大 native 看暗星全都在（满屏锐点），所以**不是丢星、不是丢能量、不是 tone**。

**根因 = 采样定理（delta 场不抗混叠）**，这是对 06-10「flux/mean」诊断的补充而非重复：
- 06-10 已对的部分：点源是 flux 语义，mean 池化按 1/N² 稀释星光。正确降采样是 **sum（保光通量）
  不是 mean（保面亮度）**；sum 池化数学上等价于"该分辨率直接从星表 binning = 原生重渲"。
- 这次补的新洞察：**即使 sum，锐点暗星在低 order 仍是孤立 delta 尖峰**。高分辨率 canvas 是一片
  delta 函数（暗星亚像素锐点、点间为零）。对 delta 场降采样是**抽样而非积分**——落在采样点上的星
  留、落在缝里的丢，密集区与暗带区都塌成噪点。要让密集处的暗星**重叠累加成连续底**、稀疏处（暗带）
  自然浮现，每颗暗星必须有**横跨 ~1 个目标像素的宽度**。这就是抗混叠预滤波 / mipmap 铁律：
  **不能对 delta 场做 mipmap，必须在生成每一级时用匹配该级像素尺度的低通（PSF）。**

**解法 = per-order 渲染，暗星 PSF 匹配该层像素**。不靠最高层往下池化；每个 Norder 用对应的
(tile_size, psf_core_px≈1px) 各从星表渲一次。低 order tile 数指数级少（N3 全天 768 个），渲得飞快。
等价于 06-10 写的"每层从星表 binning = 原生重渲"，外加"暗星给该层 PSF"这一步。

**验证（看图，非数字——本轮教训：均亮/对比度 CV 都会误导，必须肉眼看暗带可见性）**：
银道面 l20 b0 与心宿二 l352 b15 两块，同片天对比 (1) native 锐点→area 降采样 vs (2) 低 order
直接渲+暗星 PSF=1px。(1) 噪点状、暗带不可见、背景塌黑；(2) 连续发光底 + 暗带清晰浮现 + 亮星不糊。
均亮 37→52（心宿二）、64→90（银道面），但关键是**结构**：(2) 出现 (1) 完全没有的暗带河道。

**hipsgen / Aladin 能力调研（2026-06-14，本地 jar -h + 网络双查，确认无现成开关可救）**：
- **Aladin Lite v3 救不了**：major overhaul（Rust/WASM/WebGL）但仍是显示端，按 HEALPix cell 取
  预生成瓦片纹理像素（v3 论文 aspbooks.org/publications/532/007.pdf 逐字），瓦片生成时丢的结构
  恢复不了。（注：Tavily LLM 摘要曾幻觉"v3 直接从最高分辨率重采样"，论文无此说法，未采信。）
- **hipsgen 无可救开关**：池化仅 4 种 2×2 变体 `method/mode=MEAN|MEDIAN|FIRST|MIDDLE`，无 PSF、
  无抗混叠核。float 域池化有（FITS bitpix=-32），但**对 delta 场做浮点平均仍是抽样**，不解决采样
  定理问题。手册写 `treeFirst` "emphasises point sources"——保尖峰让结构更碎，反方向。彩色 JPEG
  路的 `method` 明确"for aggregating colored compressed tiles"——即 8bit 压缩域 mean/median。
- **业界（CDS Gaia DR3）就是 per-order**：存 progressive catalogue（按 order partition 源）+
  density maps（每层独立计数=该尺度天然积分），不是池化图像。旁证 per-order PSF 渲染是标准做法。
- **可省事的官方工作流（hipsgen 手册「注入外部瓦片」）**：① `minOrder=N order=N INDEX TILES`
  让 hipsgen 只建第 N 层；② 用我们自己的 per-order PSF 渲染器**替换该层瓦片**；③ 各层都这么做后
  hipsgen 只做 Allsky/properties/MOC/index.html 封装，`minOrder=order` 阻止它做会塌结构的 TREE
  池化。这样不必自造金字塔目录脚手架，且官方支持。出处：aladin.cds.unistra.fr/hips/HipsgenManual.pdf

**暗星 PSF：全层 0.6 优于"低层 1.0"（实测推翻保守估计）**：
- 当初定"暗星 PSF 匹配该层像素≈1px、N8 用 0.6"是基于"PSF 要够宽才织底"的理论。实测（心宿二
  ±3° 全 order，psf 1.0 vs 0.6 单 tile + end-to-end HiPS 在 Aladin zoom 对比）：**0.6 各层都更锐、
  星点分明，且发光底没丢**（均亮 N6 34.9 vs 32.0 几乎不变，1:1 局部密集暗星仍连成连续底）。1.0
  反而把相邻暗星胖点糊在一起（用户报"高层有点糊"）。
- 原因：Gaia 暗星位置本有亚像素抖动 + 密集区天然重叠，0.6 已足够抗混叠织底，1.0 是过度模糊。
  理论估偏保守，实测说话。结论：`--dark-psf 0.6` 全层用 0.6（pipeline 加了该参数覆盖默认）。

**渲染加速：局部窗口卷积（同轮，profile 驱动，60×）**：
- 用户痛点"渲一轮 6h"。cProfile 单 tile 定位：`_bright_star_wings` 占 81%，全在 gaussian_filter
  的 correlate1d。反直觉地**空旷 tile（174ms）比银道面密集（106ms）还慢**——瓶颈不是星数，是
  per-tile 固定开销：之前对每个 σ 档（~13 档）都在整张 (S+2margin)²（如 2070²）扩边画布做
  truncate=5 的大核高斯卷积，空旷 tile 几颗星也空转全套。
- 修法：wing 是局部的（5σ 半径），改成**每颗亮星只在它的 5σ 局部窗口贴一个预计算归一化高斯核
  ×通量**（同档星共用核），不再全画布卷。物理等价：conv(Σδ·flux)==Σ(核·flux)，远离边界逐像素
  一致（测试 `test_bright_wing_local_window_matches_full_conv` 守，内部差 ~3e-6 浮点级）；贴边
  星的越界翼直接裁掉=物理正确"翼落视野外丢弃"，比 gaussian_filter 默认 reflect 边界更对。
- 实测单 tile：空旷 174→7.5ms（23×）、密集 106→42ms（2.5×，accumulate 成主导=真计算）。
- **全天 N8 runtime 重估：6h → 3–6 分钟（96 进程，按银纬加权均 17ms/tile）**。结论：6h 不是真实
  计算量，是实现低效；**不需要 GPU / 量化 / float cache（1.5TB 写盘慢）**——重渲只要几分钟，比
  写 cache 还快。GPU 的工程复杂度（CuPy/Linux/scatter-add 移植）对 3-6 分钟的任务不值，此判断
  有 profile 数据支撑非拍脑袋。

**方法论教训（本轮踩坑沉淀）**：
- 推理对≠测得对。我先后用 8bit-vs-float 池化、tone 在哪层 两个合成实验，都没复现出问题——因为
  它们假设 canvas 已积分对、问题在后面；而真因是 canvas 在高分辨率下根本没积分（delta 场）。
- 性能问题先 profile 再优化：6h 的"瓶颈"不是直觉的星数/accumulate，是空旷 tile 的 wing 卷积空转；
  反直觉信号（空旷比密集慢）是定位线索。优化前若先上 GPU 会优化错地方。
- 均亮/对比度 CV 会骗人（合成实验里 8bit 池化的结构相关甚至 0.998）。暗带可见性是肉眼判断，
  定位这类问题必须看图，不能只看标量 metric。
- 同一个根因 06-10 就诊断过且写下架构，但"待实现"拖了 4 天没落地、期间继续在错的池化链上调参。
  教训：working.md 里标"待实现"的正确架构，要么排期落地、要么显式记为已知债，别让它沉底。

### 2026-06-10

- **核心 PSF 收窄 0.6（去糊）+ 文案合并颜色坑、裂隙转 open question。** 裂隙 PSF 诊断图（现状/无增益/窄psf0.6/无增益+窄psf）四格副产物：用户观察到窄 PSF 整体更锐不糊。A/B 三联（`outputs/_b1_clarity_compare.png`，gain3.8/psf1.1 vs gain3.8/psf0.6 vs gain1/psf0.6）确认：好看来自**窄 PSF（锐化）**而非无增益——去增益会抽掉乳光厚度（右图银河明显单薄），窄 PSF 保留增益则两头都对（暗星颗粒锐、银河厚度在、亮星靠饱和溢出照样显大）。决策：CLI `--psf-core-px` 默认 1.1→0.6（仅静态图 renderer；视频管线按用户决定保持 1.1，窄核收益在可放大细看的静态图，重渲视频极贵）。底层函数 default 保留 1.1（中性默认，测试直接调）。测试 `args.psf_core_px==0.6`，68 passed。消融阶梯 STEP 2/3/4 的 psf 同步 1.1→0.6 保持自洽（full 步用默认 0.6，避免"PSF 步 1.1、全开变 0.6"的不一致）。全套 docs/assets（hero/bortle_1-9/两 grid/消融7张）重渲转 jpg。文案：①公众号文章（`tmp/gaia_starmap_article_20260608/`）颜色去黄添为**翻车七**、标题改"七次"、裂隙**翻车六改成诚实 open question**（列消光只黑一档、深度判决实验证明加深无用、缝对比被 Gaia DR3 深度锁死）；②原理页"五个决策"→"六个"，新增决策六（白点锚定）、决策二 PSF 改 0.6 并说明为何收窄、决策四裂隙改 open question、消融代码块 1.1→0.6；③README/rfc 同步 psf 0.6。待办：chroma 仍 1.8（白点修对后建议 1.4-1.5，仍未定）；hero 图带烧入标签（既有状态，未在本轮改裁切）；裂隙配图 figure_rift_fix_pair 语义从"修复前后"变"渲染 vs 照片对比"，文件可能需重做。

- **星色白点锚定，消除银河整体偏黄（sub-agent 执行，主线程验收）。** 用户指出银盘满屏发黄，与天文摄影自然色差很远。根因：`bv_to_rgb` 是手搓线性近似、无白点锚定，且把 Gaia BP-RP 当 Johnson B-V 用，把 BP-RP∈[1,2] 的银盘主力星推成暖黄，再被 `--chroma 1.8` 放大。重写走标准做法：BP-RP→Teff 用 Pecaut & Mamajek (2013) 主序星表（太阳 G2V=0.82/5772K），Teff→线性 sRGB 走黑体普朗克谱×CIE1931 积分（与 Mitchell Charity 表交叉验证），白点锚定让太阳 5772K 落中性白（PixInsight PCC / Photoshop 设白点原理）。sub-agent 查证时识别出 stackexchange#46664（B-V 标定，太阳处给 5221K）和 Casagrande colte 系数（非单调、太阳处 7734K，疑幻觉）有问题，未盲用。A/B：银盘亮像素 R/B 1.83→1.54，band 中位饱和度 0.453→0.325，星色蓝黄分离保留。新增 `test_bv_to_rgb_g2v_white_point_is_neutral`，68 passed。chroma 建议降到 1.4-1.5（白点修对后 1.8 偏高），暂未改默认，待裂隙工作后一并定。全部正式图与素材以新白点重渲。详见 `outputs/color_whitepoint_notes.md`。

- **裂隙深度判决实验：推 G<16 收效甚微，否决。** 用户质疑裂隙不够黑是否因星表深度不够（G<13），提议推到 G<16。判决实验（用现有 ag_gspphot 反事实模拟更深截断）：① 把现有数据反切到 G<9..13，裂隙/非裂隙光比稳定在 0.48-0.53，无"越深越黑"趋势；② G<16 理论估算只把乳光比从 ~1.0 降到 0.80，离照片的几十倍对比（~0.05）差远了；③ 根因数据：缝核 A_G 中位仅 0.48（低于周围），因留在 G<13 星表里的全是尘埃前面的低消光前景星——深度推进救不了前景星填充。代价对照：G<16 全天约 9500 万颗（G<13 的 13×），下载走 ESA bulk mirror（gaia-dpch.cosmos.esa.int/gaia_dr3/bulk，HEALPix 分块 CSV，可并行 wget）但收效不抵成本。结论：裂隙问题不在深度，在前景星 + 渲染把离散前景星糊成连续面。否决 G<16，转零成本的逐星消光方向（按 ag_gspphot 区分前景/穿尘埃星，压暗后者）。

- **3D 尘埃探测实验：定性成立；定量整合遇概念分叉，v3 暂缓。** 重取全列数据（l/b/g/bp_rp/parallax/ag，五段防截断，98.9% 星有可用视差）。三视线 A_G-距离曲线签名教科书级分明：缝核 (30,+3) 在 300-800pc 阶跃（0.68→2.28 后走平，与 Aquila Rift 文献距离吻合，幕后星数同步崩塌）；亮云 (26,-2.5) 六 kpc 连续缓升（0.3→1.65）；净空 (30,+40) 平线。**但 v3（按"星身后到无穷远的实测尘埃"打折）把亮云衰减打到 0.54，比两段式的 0.96 更糟**：分布式尘埃里深处族群本有深处幸存星代理（其观测通量已自带衰减），按 behind-to-infinity 打折在任何延展分布上都重复计费。根源是增益的两种解读（按视线外推 vs 按体积代理）给出不同的衰减答案，需要清醒时决断而非凌晨换模型。处置：v3 退回 `build_render_cache_v3_experimental.py`，生产保持两段式 v2；探测数据与曲线保留。

- **显示层对齐用户参考修图（A 方案）。** 用户用 GitHub 版 hero 自行修图给出目标观感（天空真黑、带内对比强、暖色克制），参数扫描对齐后定为默认：`--target-sky 0.012`（原 0.03）、`--star-contrast 6.0`（原 4.0）、`--chroma 1.8`（新参数：亮度保持的饱和度增强，stretch 后软肩前，释放 BP-RP 自带的"中间暖两边冷"结构）。同时按用户一手后期经验撤回 rfc 中"照片曲线拉伸贡献观感差距"的说法：裂隙在真实数据里就是没有信号，亮度差距是物理欠账，后期拉的是饱和度。正式图与全部素材以新默认重渲。

- **列消光增益改两段式，修复对亮星云的误伤。** 用户复核：裂隙形状没变。显示分解诊断（逐项关闭推断光/饱和翼/对比提升测缝的弥散底）发现缝的灰底 87% 来自直接光（物理下限），而第一版全员打折公式把 Scutum 亮云窗口的推断光误砍 40%——它把分布式尘埃当薄幕重复计费，削了对比度的分母。改两段式：仅 `A_G < 0.3·A_col 且 A_col > 1` 的星（明显未穿过尘埃的前景星）按薄幕打折，其余衰减为 1。账目：云保留率 0.60→0.96，缝 0.33→0.71，对两侧均为净改进。同时实测修正了一个叙事偏差：缝内幸存暗星 A_G 均值 1.6，并非"几乎全是 A_G≈0 的前景星"。**边界结论：2D + 逐星消光到此为止，缝/云对比再进一步需要 3D 尘埃模型（视差星表逐方向消光-距离曲线），列为后续工作。**正式图与素材重渲。

- **视频管线升级 G<13 3D 星表（sub-agent 起步，主线程接力完成）。** sub-agent 给 fetch_gaia_3d.py 加了分段取数（带 300 万行截断保护）并启动取数后提前退出（以为能等自己的后台任务，看门狗形态），主线程接管。3D G<13 子集经视差质量筛选后约 690 万颗（gaia_3d_deep_g13.npz, 328MB）；用该子集自身光度函数（dlogN/dm≈0.401）推出视频增益 4.3@faint_mag_min=11（agent 留下的 derive_video_gain_g13.py）。低清验证颗粒质感与北斗连线正常后重渲两部高清与 Pages 预览。已知不对称：视频增益暂无列消光衰减（3D 缓存无 ag 列），记入 rfc 留作后续。

### 2026-06-09

- **列消光增益修正裂隙发灰（幸存者偏差）。** 用户拿真实银河照片对比，指出大裂隙在渲染里发灰、不够黑、颜色发闷。诊断（账目）：缝核 vs 亮云，G12-13 背景星计数比 9.3%，旧渲染光比 18.3%；填灰来源是前景亮星直接光（光比 35%）+ 均匀增益把缝里幸存暗星的光按无尘埃比例外推。机制是幸存者偏差：缝里留在星表里的暗星偏前景（没挨消光），增益要代理的截断后族群在尘埃后面挨打狠得多。屏幕空间形态学方案（局部密度/大尺度基线）验证失败并否决——分不开裂隙与银河带轮廓两种重叠尺度（基线 80px 时缝内 T≈1 无效果，240px 时全图 T 中位 0.575 错砍整条带）。最终方案：重取 G11-13 各段带 `ag_gspphot` 逐星消光（覆盖 57-68%，缺失用 1° 格平滑均值插补）；每格取 A_G 的 **p90** 作全柱消光 A_col（均值同样被前景偏置污染：缝核星均 1.6 vs p90 全柱 3.18）；每颗暗星的推断光乘 10^(-0.4·max(A_col−A_G,0))，即按"它身后那段尘埃"打折，直接观测光永不衰减。衰减由 `build_render_cache.py` 烘焙进正式缓存 `gaia_g13_render.npz` 的 `proxy_atten` 字段，渲染器检测到该字段自动启用。效果：缝内推断光中位降到 27%，暗带恢复黑度与结构感。`pytest` -> 67 passed。全部正式图与素材重渲。

- **星表扩到 G<13 并转正，"增益代理足够"被证伪。** 用户提议实验：截断补偿增益是否让更多真实星变得没必要。取数时踩到 Gaia 档案库匿名查询 300 万行硬上限（G<13 整表查询被无声截断成恰好 3,000,000 行），fetch_gaia_allsky.py 加 `--gmin` 分段支持，按 11-12 / 12-12.5 / 12.5-12.8 / 12.8-13 四段取回 740 万颗合并为 gaia_g13.npz（225MB）。新光度函数：各 bin 积分光通量 48.3→42.8 缓降（dlogN/dm 0.412→0.367），外推 G13-21 缺失光 ≈ G11-13 的 2.8 倍，增益 3.8@faint_mag_min=11。A/B 对比（outputs/_g13_ab_compare.png）：乳光从"粗颗粒×大增益"变成细腻奶状，暗带更锐，+4mag 列增益光变成真实可分辨星点，用户评价"差异巨大"。增益代理光占比 55%→43%。默认切换：DATA_DEFAULT=gaia_g13.npz、--faint-mag-min 11、--faint-gain 3.8。**Learning：增益代理积分光总量，代理不了颗粒统计；能用真实星就用真实星。**

- **高光软肩替换硬截断，修复 G13 银心 clip 感。** G13 的细腻乳光让 `finish_sky_adapted` 的硬天花板（y>target_white 全部压到同一值）从零散像素变成成片的无纹理平台（显示值堆在 ~212），用户反馈"很多地方 clip 了非常不舒服"。改为软肩：y 在 target_white 以上按 exp 滚降平滑映射到显示上限 3.0，膝点导数为 1，高光内部严格单调。修正后高光从 212 的平台摊开成 212-250 的连续渐变。新增单调性测试。两张正式图、消融序列、Weber 对比图全部用 G13+软肩重渲。

- **视频编码切换 H.265。** `assemble_mp4` 默认 `libx265 + hvc1 tag`（hvc1 是 Safari 识别 HEVC 的必要条件），`--codec libx264` 可回退；CLI 默认 `--crf 18` 对标旧 x264 crf 16。两部高清从保留的帧目录直接重编码（vr 334MB→258MB、前向 231MB→179MB，无需重渲染），Pages 预览用码率封顶（1000k/1400k）保持原体积、同码率下质量优于旧 H.264。预览抽帧确认北斗连线清晰。`pytest` -> 65 passed。

- **Bortle 交互改为点击选择器 + 完整竖图。** 旧交互（悬停展开行内 cover 背景图）有两个问题：展开区是横向容器、竖图被裁成一条只剩 fit-width 的水平切片；悬停触发不符合预期。与用户讨论过三个方案（九条带拼贴总览、scrollytelling、点击选择器），拼贴在当前取景下有内容混淆（银河斜向走、B1 带恰好是空天区，做了原型 `outputs/_collage_prototype.png` 验证），scrollytelling 复杂且移动端不稳，定为点击选择器：左侧九个紧凑可点击行 + 右侧稳定展示区按 9:16 完整显示对应等级竖图，移动端纵向堆叠。去掉 pointerenter/focus 自动触发，仅 click。

- **北斗连线线宽随分辨率自适应，修复高清/预览中连线不可见。** 视频移植后用户发现前向视频里北斗引导线消失。诊断：连线代码完好且开关开启，问题是线宽是绝对 1px——在 2160×2160 高清帧上本就纤细，Pages 预览又从高清 lanczos 缩到 720（旧流程是按 720 原生渲染预览，线宽相对粗 3 倍），1px 变 1/3px，再过 H.264 压缩后完全不可见。修复：`overlay_width_for_frame()` 按画幅自适应（每 720px 约 1px，2160 → 3px），`--overlay-width` 默认 0 表示自适应、显式正值仍可覆盖。验证：2160 渲染→缩 720 模拟预览，勺形连线清晰。前向高清视频与 Pages 预览/poster 重渲。`pytest` -> 64 passed。

- **新增 Pages 原理深度解读页 `docs/principles.html`。** 面向真正想理解渲染原理的读者，讲五个关键技术决策（NELM 亮度锚定、统一 PSF、饱和溢出、暗星截断补偿、人眼双阈值），配一组消融实验：固定 Bortle 1 / +2mag 场景，从朴素一星一像素画法开始逐条打开规则（5 张序列图），另加 Bortle 7 的 Weber 阈值开/关对比。全部实验图用正式 CLI 的参数开关渲染（命令附在页面上，可复现，不需要任何额外代码）：`--psf-core-px 0 --faint-gain 1 --sat-over-sky 0 --ext-threshold 0` 起步逐项恢复默认。素材在 `docs/assets/ablation_*.jpg`（裁掉烧入标签、810 宽）。主页导航与仓库区前各加一个入口。消融文案按实际渲染观感校准过（自适应拉伸下 step1 已能看出密度起伏，真实缺陷是盐粒感与星无大小差；step3 的效果是乳光增厚约三分之一、暗带加深）。

- **视频路径移植统一 PSF 成像模型（sub-agent 执行，主线程验收）。** 把静态图已转正的「统一 PSF + 饱和溢出 + 暗星截断补偿」移植到两部飞行视频，替换旧的加性 bloom（`render_3d.add_bloom` 仅作对照保留，正式路径不再调用）。关键决策：(1) 视频无 skyglow，饱和线改锚到固定参考视星等 `sat_level = sat_over_ref × L(sat_ref_mag)`（默认 6.0/6.0，单星峰值约视星等 4 等触发），独立于逐帧亮度直方图，整段视频饱和起点恒定不抖动；(2) 截断补偿按星表固有 G（g_mag）选星、按重投影视星等（vis_mag）定亮度——代理 G>11 族群是恒星身份，不随飞行改变；3D 星表实测截断 G<11 与静态图相同，faint_mag_min 9.0 / faint_gain 4.2 直接沿用；(3) 新参数全部 CLI 显式暴露，移除 `--bloom-strength`/`--bloom-sigma`。低清验证后重渲两部高清视频（4096×2048 与 2160×2160，各 600 帧）与全部 Pages 视频素材（preview mp4 ×2 + poster ×2；前向 poster 改选飞入银盘的 frame 0445，早期帧在高清下表现力不足）。`pytest` -> 63 passed。设计全文见 `outputs/video_model_port_notes.md`。

- **弥散光 Weber 阈值，修复高污染面板银河残影。** 用户反馈 Bortle scale 图里 9 级仍隐约可见银河结构，期望整体下移两级（新 7 级 ≈ 旧 9 级，8/9 级无银河）。测量银河带低频信号相对 skyglow 的对比度：B1=93%、B6=9.3%、B7=5.9%、B9=2.8%；人眼对大面积弥散光的 Weber 对比阈值在百分之几量级，低于它的结构相机拍得到但眼睛看不见，旧显示管线把任何正对比都放大了。新增 `apply_extended_visibility_threshold()`：用 `--ext-sigma 8` 高斯分离低频弥散光与高频点源，低频分量减去 `--ext-threshold 0.035` 倍 skyglow，点源可见度仍由 NELM 锚定不受影响。阈值锚定 skyglow 且刻意不随 +delta_mag 增益缩放（光学辅助把弥散对比放大过阈值后银河重新可见，机制正确）。施加后 B7 带对比 5.9%→2.5%（≈旧 B9），B8/9 <1% 基本消失，B1 仅 93%→89%。5 倍增益新旧对比图 `outputs/_weber_compare_boost.png` 确认形态。`pytest` -> 59 passed（饱和阶梯不变性测试显式关闭该阈值，因为它本就不应随增益缩放）。

- **Pages 文案重写 + Bortle 切片对齐修复。** 受众定位为天文爱好者（不预设了解 Gaia 星表或渲染管线），全部文案改为 high-level：去掉 DR3 / equirectangular / tone mapping / NELM 缩写 / pc 等术语，距离改用光年，Bortle 等级加一句通俗定义，hero 改为"120 万颗实测恒星 + 银河自己浮现"的叙事，读图边界改为"数据的边界 + 屏幕的边界"两段式。UI 修复：`.bortle-slice` 是 `<button>`，浏览器默认垂直居中内容，展开到 560px 后标签漂在画面中间；加 `display:flex; flex-direction:column; justify-content:flex-start` 置顶。eye grid 图加链接可点开原图。headless Chrome 截图（桌面 1440 / 移动 390）验证渲染。 新模型合入后，Bortle 1 / +4mag 面板整体发灰、亮斑糊成一片。诊断：`+delta_mag` 通过有效 NELM 把全部星光乘 10^(0.4·delta)（+4 即 40 倍），而饱和线锚死在 6×skyglow 不动，导致 +4 面板 25.5% 的像素被截到饱和线、36.9% 的星光摊进 3px/9px 溢出翼（+0 面板分别是 ~0% 和 1.3%）。修复：`sat_level = sat_over_sky × skyglow × 10^(0.4·delta_mag)`，饱和起点固定在距有效极限星等固定的星等深度上。扣除 skyglow 后 +4mag canvas 严格等于 +0mag 的 40 倍，饱和几何对 delta 不变，新增不变性测试锁定该行为。`pytest` -> 56 passed。两张正式图与 Pages 素材重渲染。

- **统一 PSF + 饱和溢出实验（针对亮星/暗星割裂感）。** 放大正式图后，亮星是 1px 锐点、暗星集体糊成宽 PSF 辉光，两个视觉族群之间没有过渡，亮星观感像噪点。诊断结论：割裂的根源是星点视尺寸不随亮度单调变化——双层 PSF 模型里尺寸是双峰的（锐点 vs 6px 糊层），seg_medium 里甚至反转（faint_psf 2.0 比亮星 0.75 更宽）。真实光学里 PSF 对所有星相同，亮星显大来自 tone curve 饱和与散射翼。实验：全部恒星共享 sigma≈1.1px PSF；G≥9 暗星乘 faint_gain 补偿星表 G=11 截断（用 Gaia 光度函数外推 G11-21 的缺失积分光约为 G9-11 的 5.8 倍，合理增益区间 4-7，与 seg_medium 调出的 4.2 一致）；卷积后在线性域做饱和溢出。对比输出：`outputs/exp_uniform_psf_compare.png`（旧模型 / seg_medium / uniform 三联）、`outputs/exp_uniform_psf_sat6.png`。sat_over_sky=20 只覆盖 29 像素，亮星增大效果不明显，定为 6。

- **统一 PSF + 饱和溢出转正，替换双层 PSF / seg_medium / density mask。** 实验验证后将 `accumulate_uniform_psf_stars()` + `saturate_and_bloom()` 设为唯一正式星点模型：所有恒星共享 `--psf-core-px 1.1` 高斯核；G≥`--faint-mag-min 9` 暗星乘 `--faint-gain 4.2` 补偿 G=11 星表截断；线性亮度超过 `--sat-over-sky 6` 倍 skyglow 的能量按 `--wing-sigmas 3,9` / `--wing-weights 0.65,0.35` 双高斯溢出翼能量守恒地散布。移除：`accumulate_visual_stars`（双层 PSF）、`accumulate_segmented_visual_stars`（seg_medium 实验）、`faint_star_density_mask`（统一模型下暗带由暗星计数直接呈现，无需遮罩）、三个无调用方的旧 panel 函数（render_window_panel / render_perspective_panel / render_equirect_panel），以及对应 CLI 参数。实验脚本 exp_uniform_psf.py 已并入正式 renderer 后删除。测试同步更新（饱和溢出能量守恒、截断增益只作用于暗星、视尺寸单调性），`pytest` -> 55 passed。两张正式图已用新模型重渲染。

### 2026-06-08

- 新增 `src/video_common.py`：共享的 SDR 视频渲染辅助（数据加载、缓动函数、北斗七星方向、并行帧渲染、PNG/TIFF 帧写入、H.264 mp4 合成）。M3 Ultra 主机为 32 核，`--workers` 默认 `os.cpu_count()`，工作进程直写磁盘避免 IPC 传大帧数组。

- 新增 `src/render_vr_video.py`：等距柱状 VR 飞行。新增 `src/render_big_dipper_video.py`：前向飞行（面向北斗七星中心）。CLI 参数：分辨率、帧数、帧率、worker 数、输出路径、CRF、可选 16 位 TIFF 帧保留、方向覆盖。帧目录为独立输出，ffmpeg 仅在帧全部写完后运行。低分辨率预览：`outputs/vr_equirect_lowres.mp4`（640x320, 30fps, 60 帧）与 `outputs/big_dipper_forward_lowres.mp4`（640x640, 30fps, 60 帧）。`pytest` -> 22 passed。

- **修正：共享运动轨迹，分离投影方式。** 第一次拆分错误地让 VR 和前向视频使用不同路径。新增 `src/motion.py` 统一 L 形路径（先沿银道面，再飞向银极）。两个视频使用相同位置序列，仅投影不同。前向渲染器默认改用 `perspective` 矩形投影，`--projection fisheye` 可选。首帧 QA：七颗北斗星投影在 640x640 内（x=255–373, y=288–345），若不易辨认是因为缺少连线叠加而非指向错误。`pytest` -> 24 passed。

- **时长参数。** CLI 新增 `--duration`（如 `--duration 10 --fps 60` 自动得 600 帧），`--frames` 仍可用于精确帧数。空间与时间分辨率分离。

- **前向相机修正。** 第一段运动方向改为北斗七星中心方向，第二段为银极方向；初始视线为北斗七星中心，终端视线为银心；第二段内 slerp 插值。`pytest` -> 26 passed。

- **北斗七星可见性。** FOV 收紧至 60°（首帧星点 x=187–430, y=255–371）。新增北斗七星引导线叠加层（七颗星的 3D 近似位置，逐帧重投影）。`--no-dipper-overlay` 可禁用。生成了 `outputs/big_dipper_first_frame_overlay.png` 作为 QA。`pytest` -> 27 passed。

- **路径恢复为 L 形。** 飞向北斗七星会在第一段就离开银盘（北斗七星方向接近银极），与叙事冲突。共享路径恢复：第一段沿银心方向，第二段飞向银极。前向相机独立：第一段视线为北斗七星中心（附引导线），第二段视线为 `-galactic_pole` 回望被离开的区域。

- **北斗七星优先轨迹。** 第一段目标 `big_dipper_direction * leg1_pc`，第二段目标 `galactic_center_direction * leg1_pc + galactic_pole_direction * leg2_pc`。前向视频辉光曾降至 `--bloom-strength 0.35 --bloom-sigma 3.0`，后续 Pages 预览再降半到 `--bloom-strength 0.175`。`pytest` -> 28 passed。

- **缩短第一段至 50pc。** 400pc 会飞过变形窗口。旧预览第 68 帧对应约 48.9pc。默认值：`--leg1-pc 50`, `--target-gc-pc 400`, `--leg2-pc 2500`。第二段目标独立于 `leg1_pc`：`target = galactic_center_direction * target_gc_pc + galactic_pole_direction * leg2_pc`。`pytest` -> 29 passed。

- **修正最终相机 bug。** 代码用固定的银心方向向量作终视线，从银盘上方望出去几乎平行于银盘（仅约 1,212 颗星）。改为看向银盘目标 `galactic_center_direction * target_gc_pc`（约 1,088,974 颗星）。`--end-look-dir` 可覆盖。`pytest` -> 29 passed。

- **相机转向与位置解耦。** `--look-transition-sec 2.0`：第二段开始后 2 秒内完成转向，之后注视银盘目标，位置仍在整段第二段中缓动。前向 FOV 从 60° 放宽至 90°。`pytest` -> 30 passed。

- 新增 `src/render_bortle_eye_grid.py`：Bortle 1 与 Bortle 6 × 肉眼感光度（+0/+2/+4mag）的 2×3 组合对比。默认广州地平线窗口视图（`--lat-deg 23.13`，银心中天时间），投影 `horizon_window`（后改为透视相机），归一化 `--normalization sky_median`（中位天空自适应而非百分位归一化）。输出 `outputs/knob_bortle_eye_grid.png`。命令：`python src/render_bortle_eye_grid.py --bortles 1,6 --eye-deltas 0,2,4 --output outputs/knob_bortle_eye_grid.png`。`pytest` -> 35 passed。

- **透视地平线相机。** `horizon_window` 从线性 az/alt 展开改为透视相机：水平 FOV 90°，垂直 FOV 75°，底部中心射线为地平线，相机以银心方位角为中心。色调映射增加高光压缩（`--white-pct 99.5`）。`pytest` -> 37 passed。

- **竖版默认值。** 面板尺寸 1080×1920，3×2 网格为 3240×3840，3×3 Bortle 分级网格为 3240×5760。`outputs/knob_bortle_eye_grid.png` 为正式视觉输出，SNR 模式仅用于调试。

- **SNR 调试模式。** 新增 `--mode snr`：SNR = source * exposure / sqrt(source * exposure + sky * exposure + read_noise²)。在相同总曝光下 Bortle 6 始终不如 Bortle 1。命令：`python src/render_bortle_eye_grid.py --bortles 1,6 --exposures 1,10,100 --mode snr --normalization percentile --output outputs/knob_bortle_exposure_snr_grid.png`。非正式交付物。

- **Bortle 1–9 分级序列。** 广州视图（银心约 39° 高度角），3×3 网格。命令：`python src/render_bortle_eye_grid.py --bortles 1,2,3,4,5,6,7,8,9 --eye-deltas 0 --columns-per-row 3 --output outputs/knob_bortle_scale_grid.png`。

- **背景归一化优化。** `--target-sky` 从 0.12 降为 0.03（解决 Bortle 1 发灰）。背景估计从全图中位数改为低百分位（`--sky-pct 25`）。高光压缩不再按白点整体缩放图像。Bortle 面板 p25 RGB 和稳定于约 0.365。

- **恢复恒星对比度。** 将天空底板与信号分离：`--sky-pct 25` 估计天空 → 映射到 `--target-sky 0.03` → 用 `--star-contrast 4.0` 提升高于背景的信号 → `--white-pct 99.5` 压缩高光。暗天 +4mag 面板可能高光饱和，可降至 `--star-contrast 3`。`pytest` -> 39 passed。

- **NELM 锚定。** 旧 `limiting_mag_for_sky()` 使 Bortle 1–9 的极限星等仅从 4.50 变到 4.24，与经验值差异巨大。改用经验 Bortle/NELM 表：B1=7.8, B2=7.3, B3=6.8, B4=6.3, B5=5.8, B6=5.3, B7=4.8, B8=4.3, B9=4.0。公式：`effective_nelm = empirical_bortle_nelm + sensitivity_delta_mag`。刚好处于 `effective_nelm` 的恒星赋予固定点源对比度（`--limiting-contrast` 默认 0.5），更亮星按 Pogson 标度缩放。消除了 `+2mag` 双重计数（既平移极限星等又乘增益）。极限对比度 0.5（从 0.08 提升）保持 Bortle 1 银河在 SDR 中可见。

- **双层 PSF 渲染。** 物理锚定的 1px 星点场在全宽 3240px 网格被 UI 缩小后显得空洞。改为 `--point-psf-px 1.0`（锐利点源层，亮星）+ `--psf-px 6.0 --diffuse-strength 1.0`（宽扩散层，银河辉光与缩小预览）。

- **参考拉伸。** 纯靠逐面板归一化会掩盖光污染差异。改为：每面板独立调整天空底板 → 用共享参考面板从 `--white-pct` 到 `--target-white` 计算单一信号拉伸 → 同一拉伸应用于所有面板。NELM 锚定的最终测量值 `outputs/knob_bortle_scale_grid.png`：Bortle 1 p25/50/95/99.5 = 93/102/162/286，Bortle 6 = 93/98/141/262，Bortle 9 = 93/93/96/110。`outputs/knob_bortle_eye_grid.png`：Bortle 1 +0mag = 93/94/109/150，Bortle 1 +4mag = 96/137/326/620，Bortle 6 +0mag = 93/93/95/103，Bortle 6 +4mag = 93/99/144/273。`pytest` -> 45 passed。

- **最终 Bortle 网格调优。** 宽 PSF 辉光强度降至 0.33，`--target-white` 设为 2.0。新增 `--reference-bortle` 和 `--reference-value`。感光度网格默认 `--reference-mode brightest`（Bortle 1/+4mag 为参考）。Bortle 分级网格用 `--reference-bortle 1 --reference-value 2` 匹配 `knob_bortle_eye_grid.png` 中 Bortle 1/+2mag 效果，Bortle 7–9 保持暗黑。这里的 reference 只改变显示校准，不改变物理星等或 skyglow。

- **最终渲染命令：**
  ```bash
  python src/render_bortle_eye_grid.py --bortles 1,6 --eye-deltas 0,2,4 --output outputs/knob_bortle_eye_grid.png
  python src/render_bortle_eye_grid.py --bortles 1,2,3,4,5,6,7,8,9 --eye-deltas 0 --columns-per-row 3 --reference-bortle 1 --reference-value 2 --output outputs/knob_bortle_scale_grid.png
  ```

- **高清视频渲染。** 最终命令如下：
  ```bash
  python src/render_vr_video.py \
    --width 4096 --height 2048 --duration 10 --fps 60 --workers 32 \
    --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
    --frames-dir outputs/vr_equirect_hires_frames \
    --output outputs/vr_equirect_hires.mp4

  python src/render_big_dipper_video.py \
    --width 2160 --height 2160 --duration 10 --fps 60 --workers 32 \
    --leg1-pc 50 --target-gc-pc 400 --leg2-pc 2500 \
    --frames-dir outputs/big_dipper_forward_hires_frames \
    --output outputs/big_dipper_forward_hires.mp4
  ```
  输出为 `outputs/vr_equirect_hires.mp4`（H.264，`yuv420p`，4096×2048，60fps，600 帧）和 `outputs/big_dipper_forward_hires.mp4`（H.264，`yuv420p`，2160×2160，60fps，600 帧）。`pytest` -> 48 passed。

- **Pages 视频辉光降半。** `src/render_big_dipper_video.py` 默认 `--bloom-strength` 从 0.35 改为 0.175；`src/render_vr_video.py` 默认从 0.5 改为 0.25。页面预览视频和 poster 使用降半后的 bloom 重新生成。

- **银河暗带诊断与修正。** 对旧版 Bortle 1 图做了参数、framing 和密度诊断。坐标链没有发现南北/东西翻转问题：广州纬度、银心中天时，银心方位角约 180°、高度角约 38°。Gaia G<11 数据本身也不是问题：G=9-11 暗星的星数密度图里，银心附近暗带清楚存在。旧图暗带偏弱的主因是显示层：星等加权、亮星点源、宽 PSF 和 tone mapping 把缺星区域视觉上填平了。正式 renderer 增加 `faint_star_density_mask()`，默认使用 G=9-11 暗星星数密度作为低频遮罩，参数为 `--density-mag-min 9 --density-mag-max 11 --density-psf-px 10 --density-pct 99.2 --density-floor 0.18 --density-gamma 0.9`。这一步只使用 Gaia 恒星计数，不引入尘埃 map 或银河贴图；`--no-density-mask` 可复现旧的纯星等加权辉光。重新生成了 `docs/assets/hero_milky_way.jpg`、`bortle_1.jpg` 到 `bortle_9.jpg`、`bortle_eye_grid.jpg`、`bortle_scale_grid.jpg`。

- **竖幅取景诊断。** 同一 LST=19.8、竖幅 VFOV=120° 下，默认居中银心（az≈211°）会把银心亮核放在画面中心，视觉上更像亮球；中心固定正南（az=180°）时银河更像斜向条带。诊断时发现 `horizon_window` 只传 `--look-az` 会因 `--look-alt` 缺省被覆盖为银心坐标，已改为只在缺少真正需要的轴时填默认值。输出包括 `outputs/debug_strip_vfov120_mag_density_grid.png`、`outputs/debug_strip_vfov120_param_grid.png`、`outputs/debug_strip_vertical_vfov120_lst19p8_center_south_true.png`。

## Lessons Learned

- VR 视频与前向视频共享同一个位置路径（`src/motion.py`），仅在投影和相机倾向上分化。不要在两个 CLI 里各自重新计算位置，否则出现路径不一致。

- 最终默认路径分两段：第一段沿 `big_dipper_direction()` 朝北斗方向短距离移动；第二段飞向 `galactic_center_direction() * target_gc_pc + galactic_pole_direction() * leg2_pc`，也就是银心方向上方的目标点。前向相机的视线独立插值，第一段看北斗，第二段 look-at 银盘目标点。

- 前向相机视线过渡与位置缓动是两个独立的时序控制，不要共用同一个 phase 变量。用 `--look-transition-sec` 控制相机转向窗口。

- 前向相机从高处望银盘时，终端视线必须用 look-at（指向银盘目标点），不能用固定方向向量，否则几乎看不到星。

- `horizon_window` 投影必须用透视相机（rectilinear），不能是线性 az/alt 展开，否则不符合人眼/相机视觉。

- Bortle × 感光度对比的真实物理锚点是经验 NELM（肉眼极限星等），不是凭空设计的天空直方图。务必引用经验表：Bortle 1 NELM≈7.8，Bortle 6 NELM≈5.3。不要用自拟合的 SNR 模型替代：旧 `limiting_mag_for_sky()` 根本拉不开 Bortle 阶梯。

- NELM 是给定天空背景下的裸眼极限星等，不是眼睛硬件的固定规格。`+2mag` 表示在当前 Bortle baseline 上把有效极限星等提高 2 等。

- `+2mag` 不应同时平移极限星等又乘渲染增益（双重计数）。感光度变更只应改变 `effective_nelm`，所有恒星亮度按 Pogson 标度相对该极限缩放。

- 显示拉伸必须基于共享参考面板（user-specified reference），不能逐面板独立拉伸到 white，否则归一化会掩盖光污染差异。

- 恒星视尺寸必须随亮度单调增大，这是星野"看起来真"的关键。双层 PSF（亮星锐点 + 全员宽糊层）让尺寸分布双峰化，放大后亮星像噪点；seg_medium（暗星 PSF 比亮星宽）直接反转单调性。正确做法是真实成像模型：统一 PSF + 亮端饱和溢出，视尺寸由亮度通过饱和机制连续决定。

- PSF 的实现是整幅 canvas 的一次 `gaussian_filter`，代价与星数无关。"几十万颗暗星要不要单独 PSF"是个伪问题，不存在每星成本。

- 显示层里每个阈值都要明确它锚在哪个量纲上。星光随 delta_mag 整体缩放时，锚在 skyglow 上的饱和线不会跟着走，高 delta 面板就大面积截断。凡是和星光比较的阈值，都应该表达成"距有效极限星等的星等深度"，让它随亮度阶梯平移。

- 银河乳光和尘埃暗带是同一份信息的正负两面：都来自暗星计数。给截断前最后一段暗星乘补偿增益（从光度函数外推），乳光和暗带同时出现，不需要单独的 density mask 或宽 PSF 辉光层。增益的物理含义是让观测到的暗星代理截断之后的不可分辨族群。

- 用观测样本做外推时，先问样本是被怎么筛选出来的。尘埃方向的幸存暗星偏前景，它们的光、甚至它们的逐星消光均值，都不能代表被挡在后面的族群；全柱量要从样本的高分位（深处幸存者）里取。

- 增益代理的是积分光总量，代理不了颗粒统计。"总光通量补对了就不需要更多真实星"这个判断被 G11→G13 的 A/B 证伪：同样总光，少量粗颗粒×大增益和大量细颗粒×小增益在 1:1 下质感差异巨大。能用真实恒星就用真实恒星，增益只补真正不可分辨的部分；星表截断星等本身是个显示质量参数。

- Gaia 档案库匿名查询有 300 万行硬上限，超限被无声截断（行数恰好等于 3,000,000 是警报信号）。取暗星段必须按星等分段查询再合并，每段留余量。

- 缩略图里银河断裂的问题应在输出层解决（线性光域先降采样再 gamma 编码），不要为此在渲染层把暗星预先糊化。

- `--target-sky` 设为 0.03（不是 0.12），背景估计用低百分位（`--sky-pct 25`）。全图中位数会被大面积银河拉高，导致暗天面板发灰。

- `outputs/` 已在 gitignore 中，帧目录（`--frames-dir`）和最终 mp4/png 均落在此目录下。帧目录是独立输出，ffmpeg 只在帧写完后运行，避免帧数据通过 IPC 传回主进程。

## 大裂隙宽度调研（2026-06-10，结论：受 Gaia DR3 深度物理限制，非渲染缺陷）

用户对比实拍：真实大裂隙又宽又长、几乎完全没有信号（对比度约几十倍），我们的渲染只在小范围有缝、宽度远小于实拍（对比度约 2 倍）。用户提出的关键悖论：Gaia 是可见光巡天，若星被尘埃挡住根本不会出现在星表里，为什么直接画 Gaia 自己的星反而看不到裂隙？

系统排查了五个方向，全部收效甚微，因为都不是主因：

1. **逐星增益消光衰减**（`_rift_perstar_ab.png`，k=1/2）：几乎无变化。增益只作用于"不可分辨光"那一小部分，而显示分解显示缝区约 87% 的光是直接观测到的真实星光，增益碰不到。
2. **两段式列消光**（`build_render_cache.py` 生产版 v2）：薄幕折扣只对"明显未穿过尘埃"的前景星生效，重建后 atten p10/50/90 ≈ [1,1,1]，对缝形状无可见影响。
3. **3D behind-to-infinity 衰减**（`build_render_cache_v3_experimental.py`）：会重复计费、误伤 Scutum 亮云（亮云被压到 0.54），已退回实验状态。
4. **加深星表 G<13→G<16**：判决实验（反向裁切 G<9..13）显示缝/亮区光比稳定在 0.48-0.53；外推到 G<16 也只到 0.80，离实拍的约 0.05 差得远。缝核 A_G 中位数仅 0.48——因为留在星表里的是尘埃前面的前景幸存星。代价大、判决实验说没用，否决。
5. **收窄 PSF**（`_rift_psf_diag.png` 四宫格：现状 gain3.8/psf1.1、无增益、窄 psf0.6、无增益+窄 psf0.6）：四个版本缝里的填充几乎一模一样，缝没变黑。证明缝里的光不是 PSF 把离散星抹成连续面造成的，而是真实星点本身就这么密这么亮。

**真数据决定性验证（2026-06-10 晚，`src/probe_rift_depth.py`）**：上面第 4 点是从 G<13 外推的推断。后来发现广州银心 FOV 的完整 Flatiron 深星表（到 ~21 等）已在本地（见 `docs/data_manifest.md`），于是直接测了真数据。定两个 2° 圆窗——裂隙核心 (l=1.5,b=4.5) 与亮云对照 (l=330.5,b=-2.5)，读覆盖的 47 个分片（~10GB），对每个星等阈值算积分光通量比（不用星数比，因为渲染灰度来自光通量）。结果（rift/cloud 光比）：

| G< | 缝/云 星数比 | 缝/云 光比 |
|---|---|---|
| 11 | 0.144 | 0.290 |
| 13 | 0.131 | 0.225 |
| 16 | 0.340 | 0.244 |
| 18 | 0.551 | 0.296 |
| 20 | 0.684 | 0.330 |

**加深整整 9 个星等，光比只从 0.29 动到 0.33（1.14×），离照片的 0.02-0.05 差一个数量级，而且方向是错的——缝反而略微变亮。** 机制看星数比那列就懂：G<20 缝后面的暗星确实涌进来了（星数比 0.13→0.68），但这些暗星太暗，几百万颗的积分光抵不过亮云方向同样在涨的光，两边一起涨，比值不变。**这把"加深星表能压黑缝"彻底证伪：不是数据深度不够，是再深的暗星也补不进缝的对比，问题根本不在深度。**

**全量真渲的视觉终判（2026-06-10 晚）**：用户要求不停在数字、真把图渲出来看。build 了覆盖整个广州 FOV 的深星表缓存（`src/build_fov_deep_cache.py`，16 worker 并行解压 2044 片，`data/raw/fov_g20.npz` 6.16 亿星 G<20），用正式渲染器（faint_gain=1，深星表已含真实暗星不补增益）渲了 G<13/G<16/G<20 三张同视角广州银河（`outputs/_fov_g13_g16_g20.png`）。结果：从 G<13→G<16→G<20，银河越来越亮越满，裂隙两侧亮云越来越突出（真数据比增益外推更符合用户的天文摄影修图手感——"大裂隙两侧的银河特别亮"），但**大裂隙的连续宽暗带形态自始至终没有出现**，三张图都只有零碎不连续的暗带斑块，加深 250 倍星数反而让暗带在更亮乳光衬托下更零碎。**视觉证据与 probing 光比表双重确认：加深星表无法让大裂隙浮现，问题不在深度。** 副产物认知：增益外推够用但不如真深星表（真星在裂隙两侧亮云上有增益给不出的真实结构）。

**仍未排除、最可能的真因（待验证）**：我们没有对恒星做消光减光。真实大裂隙之所以黑，是缝后面整片恒星被尘埃整体压暗几个星等；我们把每颗进了星表的星画成它"本该有"的亮度，缝后面的星被画得跟没尘埃挡着一样亮，于是缝被填亮、大裂隙形态消失。Gaia 的 G 星等本已含消光（是被压暗后的观测值），所以不能简单再减一次（那是穿两次尘埃），但"为什么观测里黑的缝在我们渲染里不黑"这一环还没有干净的答案，是下一个待查的物理机制。

**tone mapping 突破——裂隙其实部分是显示层问题（2026-06-10 晚）**：用户看 G<20 全量图后的关键观察：裂隙正在出来，机制不是"缝变暗"而是"两侧真实亮云 consistently 变亮、细节变多"，把缝/云的整体反差拉开了——之前所有"缝核单点光比"量错了维度。而 G<20 当前 tone mapping 把裂隙两侧亮云**过曝**了，高光糊成一片，反而盖住缝。在保存的线性浮点画布上（`render_fov.py --save-linear`，47M，秒级迭代不重渲）sweep 高光软肩膝点 `target_white` 2.0→1.4→1.0→0.7：压高光越狠，亮云高光退回、云里暗带细节越清晰，**裂隙越连续**。定 target_white=1.0（高光与整体亮度平衡点）。结论修正：真实暗带信号一直在 G<20 数据里，被过曝高光糊住了，压高光救回——**"为什么缝不黑"部分是 tone mapping 问题，不全是消光物理**。消光减光仍是值得查的方向，但 tone 这一刀证明显示层有真实可挖的对比。`src/tone_iterate.py` 是浮点画布上的 tone 迭代工具。

**ablation：奇技淫巧→以力破巧（2026-06-10 晚，`outputs/_ablation_depth_gain.png`）**：6 张同视角同 tone 对比——G<11+gain3.8 / G<13+gain3.8（增益近似组）| G<13 / G<16 / G<18 / G<20（真星无增益组，星数 2.4M→33M→150M→616M）。叙事：数据稀疏时增益是聪明的奇技淫巧（少量星×光度函数外推补出乳光，但糙、偏假）；数据量上来后以力破巧——真实星自然涌现出比近似更细腻饱满的乳光、更有结构的暗带、更亮的裂隙两侧，无需任何 trick。G<20 真星明显优于 G<11/13 增益版。

**工程修复（亿级星表渲染，2026-06-10 晚，全部 68 测试通过）**：3300 万星渲染一度吃 94GB、6.16 亿星 OOM 把机器搞卡。定位并修四处（均与原版逐像素一致）：① `bv_to_rgb` 逐星算黑体谱产生 N×n_wl 巨型中间量（94GB 元凶）→ 4096 档 LUT 查表，94G→8.7G；② `accumulate_stars` 的 `np.add.at` → `np.bincount` C 实现桶累加；③ 累加 weights 的 N×3 中间量 → 分块累加；④ **性能瓶颈定位（profiling G<20）：坐标变换 gal_to_altaz(52-82s)+project(24s) 占 68% 时间且是内存峰值，全单线程 O(N)**。`gal_to_altaz` 的 astropy SkyCoord（亿级星点构造对象+高精度框架转换，几十秒+几十 GB）→ 预导出 J2000 旋转矩阵纯 numpy 直算（数值差 1e-12°）；整条逐星管线并行（`src/render_fov.py`，28 worker，每 worker 累加到自己的线性画布、主进程求和，显示链复用 `normalize_panel` 在主进程做一次保证逐像素一致）。结果：G<16 142s/114G → 3.7s/6G（40×），G<20 稳定 95s/10G，不再卡机器。教训：早先手搓 `render_fov_parallel.py` 把显示链搬进 worker 复刻错导致过曝（用户一眼看出），已废弃——并行只到"线性画布累加"，非线性/全局的显示链绝不进 worker。

**根因（真数据确认）**：缝的发灰，从 G<11 到 G<20 全程由消光压不没的前景星/较亮星主导，它们在缝和云两方向的相对关系几乎不随星等深度改变。缝的消光只有约 2 等，压不没前景星。真实照片那种几十倍的纯黑对比不是靠星表更深能得到的——它来自激进的曲线拉伸（显示层操作）叠加长曝光在更极端深度上的消光咬死，二者都不是"只画真实星、不叠贴图"的纯涌现渲染能复现的。

**立场结论**：用 Gaia DR3 + "只画真实星、不叠尘埃贴图"的诚实渲染原则，这条缝就到现在这个程度。这一结论已从 G<13 外推升级为 G<20 真数据实测，证据更硬。要突破要么破立项原则显式叠加三维尘埃消光外推/激进曲线拉伸，否则缝的对比就锁在这。决定接受这个边界，在原理页/文章里把它写成诚实的 open question，不追求逼近照片。probing 脚本 `src/probe_rift_depth.py` + 窗口定位 `src/probe_rift_windows.py` 留档。

**副产物（与裂隙无关但有价值）**：PSF 诊断图里"无增益 + 窄 PSF（0.6）"那一格星点更锐利、整体不糊，视觉上更耐看。已用该参数（gain3.8/psf0.6）重渲 Bortle 1 并转正为默认（PR #17）。

## 超大图 / Aladin HiPS 金字塔的两个致命 bug（2026-06-10 深夜，用户 + Fable 共诊）

目标：渲超大图（24K→10亿像素）喂 Aladin Lite（带 WCS 的多图 → hipsgen 自动拼金字塔），zoom in 看锐利单星、zoom out 看乳光银河。踩了一连串坑，最后定量确诊。

**现象**：同样 6.16 亿星，12K（psf0.6）渲出来比原生 1080 暗一个量级（信号中位 0.0102→0.0005），降采样回 1080 也救不回（中位卡 0.0001，比原生低 100×；8-bit 图中位 20 vs 47、p99 25 vs 181，怎么调 tone 都拉不起来）。养成习惯：**渲完图必 plot color histogram 和原生 1080 参照对比 p99/中位，对不上就别往下走**（用户教训：一直用缩略图肉眼看会被骗）。

**根因 = 两个独立 bug 叠加**：

1. **降采样语义错（mean vs sum，Fable 诊断）**：点源渲染里星光是 **flux 语义**（每像素值 ∝ 像素立体角 Ω，分辨率越高每像素越暗），skyglow 是 **radiance 语义**（常数底，不随 Ω 变）。block-**mean** 守恒面亮度、把 flux 星光按 1/16 稀释 → 星光/天光比掉 16×。正确是 **block-sum 池化**（保光通量），sum 池化在数学上严格等价于"该分辨率直接从星表 binning"，每层都成为原生渲染。验证：no-Weber + sum 降采样后 p90=0.0359/p99=0.151，命中原生 0.038/0.151。

2. **Weber 阈值误杀乳光（用户 hypothesis）**：`apply_extended_visibility_threshold`（人眼弥散光对比阈值，减掉低于天空背景百分之几的弥散结构）在能量摊薄后把大片低对比乳光直接清零，且发生在 tone **之前**，不可逆，tone 救不回。验证：关掉 ext_threshold，降采样后 %above 63.5%→95.8%、p90 回升 3×。

**正确架构（用户 + Fable 共识，待实现）**：① 渲最高分辨率线性 float 画布（小 PSF 保锐星、**不加 skyglow、不加 Weber、不 tone**）；② **sum 池化**逐层建金字塔；③ skyglow + Weber + tone **在每一层降采样之后独立做**，floor 用已知常数对齐不从图像估计。绝不能让 hipsgen/Aladin 对 8-bit tone 过的瓦片做 mean 平均（gamma 域 + clip 后能量不可逆，zoom out 乳光出不来；DSS 能 zoom 是因为底片本身是 sky-limited 面亮度数据，每层原生就有乳光）。

**单星峰值与分辨率无关 ≠ 物理正确**：这是 flux-deposit 渲染约定的签名（每颗星把全部 L 塞进固定像素、PSF 以像素为单位）。真实固定角 PSF 下峰值应随像素变小而降。这解释了为什么"小 PSF 大图"必须配 sum 金字塔 + 逐层 tone，单张大图 + 全局 tone + mean 金字塔数学上不可能同时给出锐星和正确乳光。

## TAN 投影 + WCS + hipsgen 全链路 + 立体角归一化（2026-06-10 深夜）

**hipsgen 链路打通**：地平投影没法写标准 WCS（地平坐标随时间变）。改用银道 TAN(gnomonic) 投影，以银心 (l,b)=(0,0) 为切点（`src/render_tan_wcs.py`），输出 PNG + 同名 `.hhh`（FITS WCS：CTYPE=GLON-TAN/GLAT-TAN, CRVAL=切点, CRPIX=图中心, CDELT=度/像素）。`java -jar AladinBeta.jar -hipsgen in=dir color=png` 自动读 WCS、重投影、切 HiPS 金字塔（Norder0-3 + Allsky + MOC + properties）。验证：WCS 正确解析（target→银心 ra=266.4/dec=-28.9），3.4s 生成标准 HiPS。Java 26 不兼容旧 jar（JApplet 已移除），需 `openjdk@11`。注意 properties 里 `hips_hierarchy=median`——Aladin 金字塔降采样用 median，对高分辨率多图会重蹈乳光丢失（见上一节 sum-vs-mean），高清成品需自己 sum 池化建层、别让 hipsgen 做平均。

**TAN 图"偏暗"的真因 = 每像素立体角不同（用户 push back 逼出）**：错误论证——我曾用"TAN 构图不同、银心一条带、拉低中位是正确的"搪塞。用户反驳：tone 是全局的、数据是全局的，为什么会不一样？正面查线性画布证实：TAN(40°/1024, 0.039°/px) vs 广州地平(90°/1080, 0.083°/px)，p99=0.057 vs 0.150（差 2.6×）。根因是**星光是 flux 语义**——每像素值随像素角面积 ∝Ω 变化，像素立体角越小每像素收的光越少，所以 TAN 暗。这不是 tone 能救的（sweep star_contrast/target_white 中位铁卡 24-25，证明病在画布不在 tone）。**修法：立体角归一化**——accumulate 后每像素 ÷ 像素立体角（×REF_OMEGA/cdelt²，REF_OMEGA 取广州像素 0.083² 当量），把 flux 转成面亮度 radiance，与投影/分辨率/fov 无关，一套 tone 通用，且金字塔 sum 池化后自洽。验证：归一后 TAN 图逐分位命中 ref（中位 48 vs 47、p99 185 vs 181）。这张归一化 TAN 银心图是目前最好的银河成品。

**方法论锁定**：渲完图必 plot histogram 和 ref 对分位，对不上别往下；不用 local 视觉印象解释 global 统计差异。

## HiPS 瓦片化的接缝 + fading 修复（2026-06-10 深夜）

把 FOV 切成 TAN 瓦片（fov=20/step=16 重叠 4°，或 10 亿像素版 fov=6/step=5）交 hipsgen 拼金字塔后，用户在亮处看到接缝（暗处看不出，两边亮度无差异）。

**接缝根因**：`hips_overlay=mean`，重叠区把两张瓦片 mean 平均；而重叠区恰好是两张瓦片**各自的边缘**（实测：重叠区一颗星在相邻两瓦片各落 px≈1820 和 228，都离边缘仅 ~228px、离切点 7.7°≈fov 半宽），gnomonic 投影边缘畸变最大、两张畸变方向相反，mean 混合→内容错位/重影→接缝。亮处星密看得清，暗处无星看不出。

**修法对比**：
- `border=205`（裁掉每张瓦片边缘畸变区）→ **错**：裁过头，瓦片之间露出黑线 gap，比接缝更糟，弃。
- `fading=true`（重叠区羽化过渡，不裁边）→ **正解**，用户确认"完美"。hipsgen 专为消重叠接缝设计，让重叠带平滑混合糊掉错位。

**完整 HiPS 流程（定稿）**：
1. 渲带 WCS 的 TAN 瓦片（`render_tan_wcs.py --tiles`，多进程并行，立体角归一化，手性自洽 +xi/CDELT1>0）。
2. hipsgen：`java -Xmx80g -jar AladinBeta.jar -hipsgen in=tiles out=hips color=jpeg "target=271.672 -25.873" fading=true`（jpeg 小、target 放 FOV 中心银道5,-2.5、fading 消缝）。10 亿像素版 338 瓦片 → Norder0-6 七层金字塔、13322 瓦片、1.2G、~16min。
3. host：page 留 github.io，HiPS（1.2G，github 放不下）放 yage（Cloudflare 后），跨域靠 Cloudflare 加 `Access-Control-Allow-Origin` 头。低分辨率用 4K 单图 JPG 嵌 page，高分辨率用 Aladin Lite 指向 yage HiPS。

**注**：preview.html 我生成的 createImageSurvey 调用 Aladin Lite v3 不认（空天球），用户用自己的方式看 HiPS，不再生成 preview.html。

## 进行中状态（2026-06-10，compact 前快照）

**已定稿产物**：
- 10 亿像素 HiPS：`outputs/_hips1b_out`（1.4G，Norder0-6，13322 jpg，fading 消缝，用户改对的 tiles + 调亮）。源瓦片 `outputs/hips1b_tiles`（338 张 2048² + .hhh，手性 +xi/CDELT1>0 自洽）。
- zoom 视频终版：`outputs/zoom15_h265_hold.mp4`（19 秒=首停1s+zoom15s+尾停3s，H.265 hvc1 QuickTime 兼容，CRF24，16M）。450 帧保留在 `outputs/zoom15_frames`（用户修过，是新版）。前 10 秒 70°→1.9°、后 5 秒 1.9°→0.25°。脚本 `src/render_zoom_video.py`（--frames-dir/--keep-frames 保留帧；本次 15 秒续接是手动补渲 f0288-0449）。

**待办任务**（用户一连串新指令，task #37-40）：
1. **#37 改 `_hips1b_out/index.html` 好看**：参考 `outputs/DSSBackendFrontend`（完整前端项目 dss.js/dss.css/index.html，Aladin Lite 美观嵌入范例）。这是 yage 上的高清浏览落地页。
2. **#38 更新 GitHub Page**（`docs/index.html` 保持现设计）：用最新成果更新文案（颜色白点去黄、G<20 深星表 6.16亿星、ablation 奇技淫巧→以力破巧），embed 素材；末尾加"高清大图"区：zoom 视频自动播 + 4K 标准图并排 + 链接到 yage HiPS（URL 待用户 rsync 后给）。流程：我做好→用户 rsync 上传→给 URL→我填进 page→PR merge。
3. **#39 L 型视频用全量 G<20 重渲**：`src/render_l_video.py`（3D 飞行，北斗起步两段叙事，import render_3d，当前用 `data/raw/gaia_3d_deep.npz`）→ 迁移到全量深星表。注意 L 视频是 3D 飞行（需 ra/dec/parallax/dist），fov_g20.npz 只有 l/b/g/bp_rp，可能需要 3D 版深星表或 build。
4. **#40 output cleanup**：保留 zoom15_frames / hips1b_tiles / _hips1b_out / ablation_*.png / DSSBackendFrontend / 最终视频；已删 12K 全部 + 过时视频版本。其余中间产物可删。

**host 方案（已定）**：page 留 github.io，HiPS（1.4G 超 Pages）放 yage（Cloudflare 后），跨域靠 Cloudflare 加 Access-Control-Allow-Origin 头。低分辨率 4K 单图嵌 page。

---

## 进行中状态（2026-06-10，compact 后续）

完成本轮 orchestration，四件事：

**#37 HiPS 落地页（done）** — `outputs/_hips1b_out/index.html` 重写为暗色/金色风格的 Aladin Lite 浏览页，与 GitHub Pages 同族视觉。删掉了 DSS 参考里全部 survey-planner 功能（相机/马赛克/CSV/DSO 搜索），只保留"界面"：slim 顶栏 + 极简介绍 + Aladin viewer 主体 + 三张 .stat 元数据卡 + footer。Aladin v2 wiring 原样保留（同 survey id GaiaMW1B、目录相对 hipsDir、不升 v3）。

**#38 GitHub Page（done，待 URL）** — `docs/index.html` 新增 `#hires` 高清大图区（在 #flight 与 #boundary 之间）：左 4K 静图 `assets/fov_g20_4k.jpg`（由 `_fov_g20_12k_psf06.png` 降到 2160×3840），右自动循环 zoom 视频 `assets/zoom_milkyway.mp4`（H.264 重编码，640² 方形，原 HEVC 在 Chrome/Firefox 不解码故转码）。文案讲清"换了数据：七百万亮星 → 六亿全量 G<20"。底部 CTA "打开十亿像素浏览器" data-yage-link href="#" 占位，JS 在 href 仍为 # 时自动隐藏整个 CTA panel。**待用户 rsync HiPS 到 yage 给 URL，填入即可。** 还修了 principles 区一个破折号。

**#39 L 视频重渲（in_progress）** — 关键决定：用户说"全量 G<20"物理上不可行（3D 飞行需 ra/dec/parallax/dist，fov_g20.npz 无 3D；且 Gaia 视差 G>~13 噪声主导，飞进去违背"数据边界要诚实"题眼）。改用 G<13（`gaia_3d_deep_g13.npz`，7.1M 星，6× 旧 1.2M）。runner `src/_run_l_video_g13.py` 后台渲染中（约 310/600 帧），产物 `outputs/l_flight_g13.mp4`。已记忆到 project_gaia_l_video_g13_ceiling。

**#35/#40 cleanup（done）** — outputs 清到 5.2G。删了全部 `_*` 调试/probe/12k/abl/fov/tan PNG、`*_lin.npy` 中间产物、`zoom15_frames copy` 重复目录、`_tan_*.hhh` 实验头。保留：`_hips1b_out`、`hips1b_tiles`、`zoom15_frames`、`l_video_frames_g13`、ablation 系列、knob 网格、final mp4、DSSBackendFrontend、tmp_reference_hips（含 AladinBeta.jar）、notes md。

**下一步**：L 视频渲完 → 合成 mp4 → 给用户看 → 决定是否进 docs/assets + 文案。GitHub Page 等 yage URL 后填 CTA → PR merge。

---

## 独立 PR：广州图修复 + bortle grid 升 G<20 + 原理页 5 步重写（2026-06-10 晚）

- **广州主图错版修复**：assets 里 `fov_g20_4k.jpg` 被塞成了 Weber-killed 版本（弥散光被压死、银河缩成中间一小团），用户的备份 `outputs/_fov_g20_12k_noweber.jpg`（G<20 关 Weber，大裂隙 profound）才对。用它重压 4k（2160×3840）。**hero 图同源**：用户确认那张备份"超屌"，hero 不重渲，直接把同一张备份缩成 1000×1778 当 hero。两图同源不同尺寸。
- **bortle grid 升 G<20 关键坑：Weber 必须开。** 第一次重渲两个 grid 时我让 sub-agent 传 `--ext-threshold 0`（关 Weber）——对主图/高清大图对（要展示全部弥散结构），但对 **bortle 分级序列是错的**：Weber 阈值正是"银河在城市里消失"的机制，关了它高污染面板（B7-9）弥散光不被人眼阈值砍掉 + G<20 暗星暴增，B9 亮得像旧版 B5（用户一眼看出"像 vibrance threshold 没开"）。修复：grid 重渲升 G<20 + faint_gain 1 + target_white 1.0，但 **Weber 走默认 0.035（开）**。eye_grid 同理。`render_bortle_eye_grid.py` 已被 sub-agent 加了 `--workers N` 并行路径（坐标预计算一次多面板复用，bit-exact，77 测试通过）。
- **原理页按用户新 5 步骨架重写**：① 每颗星一个像素（无坑，诚实地基）② PSF + 增益（合并旧决策二+四）③ 饱和溢出 ④ 加数据（scale-up：奇技淫巧→以力破巧，放 `ablation_scale_up.jpg` 五联 G<13gain→G13/16/18/20 真星）⑤ Web 预置与人眼阈值（Weber）。颜色白点 + 裂隙 open question + 亮星缺席作为"颜色与边界"收尾。
- **yage URL 填入**：`https://yage.ai/gaia_milky_way/` → CTA「打开十亿像素浏览器」。

---

## Weber 硬补丁的真凶：signal_stretch 逐图自适应（2026-06-10 深夜，metric 驱动定位）

用户连续观察 + 一个量化诊断器定位了根因，**不是 Weber 太硬**。

**诊断器**（`src/_weber_diag.py` → 将固化为 validator）：两个解耦的物理量。
- visibility = 银河带 vs 天空底的对比，量在 **线性 + Weber + skyglow 后、tone 前** 的画布（不能量 tone 后 uint8，逐图自适应会抹平亮度差）。
- band 显示亮度 = tone 后 uint8 band 中位，揭示"显示出来到底多亮"。
- hardness = tone 后银河带边缘梯度 p98 / 带峰值（空间锐利度，硬补丁→高）。

**关键数据**（干净画布套不同 bortle，360×640）：
- 现状逐图 signal_stretch=None：band 显示亮度 B1=287, B6=283, B7=273, B9=208 —— **几乎不随 bortle 变**，B9 majestic 得像 B1。这就是"拆成单图后 B7 像 B0"的根因。
- global signal_stretch（用 B1 算一次定死，所有图共用）：band B1=287, B6=105, B7=87, B9=66 —— **单调递减**，光污染如实抹掉银河。✓

**根本 bug**：`normalize_panel` 的 `signal_stretch=None` 时逐图算（`signal_stretch_for_adapted` 把每图银河信号都拉到 target_white），于是 B7 微弱银河被各自拉成 majestic。`render_fov.py` 单图路径正是 None，grid 路径有共享 stretch 才侥幸对。**白点/信号拉伸补偿必须 global（一个从 B1 暗空参考算出的固定常数），不能逐图。** sky_floor 归一可保留逐图（眼睛适应天空背景，对的）。

**linear visibility 是比值、对均匀 rescale 不变**，所以它对这个 bug 盲（B1/B6/B9 比值不变）；真正暴露 bug 的是 **tone 后 band 显示亮度的绝对值**。验收判据：tone 后 band 中位亮度必须随 bortle 单调递减（B1≫B6>B7>B9，B9 接近天空底）。

**待修**：把 global signal_stretch 固化进架构，render_fov + grid 单图路径都用它。Weber softness（对比域 sigmoid）作为正交的"硬边羽化"手段保留，但它不是主因——主因是 stretch。

---

## PR1 收尾：场景/观测者解耦 + sky-floor 物理锚（2026-06-10 深夜，物理路定案）

接上节。Fable5 的深度诊断把真凶定到比"stretch 逐图"更底层的地方，并给出物理正确的修法。**机器在此前一次渲染中因 OOM 硬死机**——根因是误入 `render_bortle_eye_grid.py` 的串行 `render_grid` 路径（无 --workers，逐面板重投影 6.16 亿星、不分块）。教训：bortle 系列一律走 render_fov.py 的 `--sweep-bortles` 路径，workers≤16，串行多面板路径禁用。

**真凶（比 stretch 更底层）**：`render_fov.py` 的 worker 用 `visual_luminance_for_mags(g, bortle, ...)` 算星亮度——这把一个**随 bortle 变的常数 k(B)** 烤进了线性画布本身。于是"场景"（星的物理光通量）和"观测者"（光污染等级）被耦合，所谓"共享 B1 stretch"是在一张随 bortle 漂移的画布上算的，stretch 跟着漂（实测 5.26→3.92→3.13）。

**物理正确修法（已实现，弃掉所有 hack）**：
1. **场景/观测者解耦**：`--sweep-bortles` 模式把场景渲一次（星亮度锚死在 `--scene-ref-bortle 1`，与 bortle 无关），bortle 只进显示链（sat∝sky(B)、Weber vs sky(B)、加性 skyglow(B)）。6.16 亿星只渲一次（~23s），B1-9 变成对一张画布的秒级显示链 sweep。**顺带把渲染成本降 9 倍、且 renderer/validator 共享同一画布，脱节不可能再发生。** RSS 稳定 ~15GB（mmap 星表主导），不再 OOM。
2. **sky-floor 锚死物理常数**：`adapt_sky_floor` 新增 `sky_anchor` 参数，传 `3.0 * skyglow_level(bortle)`（×3 因 y=sum(RGB)，skyglow 加在三通道）。不再用图像百分位估 sky floor——百分位估计正是逼着 star_contrast 撑到 6× 凑对比、再把 post-Weber 残留吹成硬斑的根。锚死后对比由物理定，银河该淡就淡。（实测 star_contrast 6→3.5 对 band/sky 比值无影响：signal_stretch 锚在 B1，star_contrast 在比值里抵消。）

**弃掉的死路（别再走）**：
- **频段拆分对比预算**（高频点星拿满增益、低频弥散带拿低增益）：用户一眼判为 artificial 二元 hack，非物理。已完全 revert。
- 在亮部（B1/B6 银心）测 Weber 软膝差别：Weber 只对暗弱弥散光起效，亮部测不出。
- 在小数据集（fov_g13 2.4M）验证：无 G<20 暗星 = fallback 到老的无 artifact 版，defeats the purpose。**bortle 系列一律在 fov_g20 6.16 亿星上验证。**

**最终参数（render_fov.py 默认）**：`--ext-threshold 0.04 --ext-softness 0.5`（对比域 sigmoid 软化，高 bortle 银河柔和渐隐而非等高线硬块）。用户要 B7 近不可见、仍柔和——达成：B7 是银心一小撮柔光淡入黑，无硬边。

**验收器** `src/validate_bortle_series.py`（吃真 PNG，三个解耦量）：
- contrast=(band-sky)/sky（Weber 对比），texture=band p90-p50（防糊），hardness=过渡带 log 梯度按崖高归一（防硬斑，亮度/分辨率不变）。
- 两个 washout-tail 豁免：(1) contrast<0.20 时豁免 hardness（band 已消退，崖高→0 使 hardness 除零退化，"边缘硬不硬"在无边缘时无定义）；(2) 相邻档都<0.20 时单调放宽到 ≥（两档都正确归零并列在天光底是对的）。
- 全分辨率 fov_g20 实测：B1 contrast2.55/texture111、B5 0.63、B7 0.05(豁免)、B9 0.00，全 PASS。亲眼核实 B1 majestic、B5 清晰、B7 柔淡、B9 近灭，全程柔和无硬斑。
- 77 测试通过（解耦/锚均向后兼容：sky_anchor=None、value 路径不变）。

**eye_grid 留待重构 PR**：它走的是带 sensitivity(+0/2/4mag) 维度的旧 grid 路径，未呈现硬斑 bug（+sensitivity 列保持银河亮），不属 PR1 修复范围；现 sweep 模式 value 固定 0，补 sensitivity 维度是 scope creep，挪到 PR3 一起做。

---

## 光污染强度真旋钮：SKYGLOW_POLLUTION_BOOST（2026-06-11，并入 PR1）

用户复核 PR1 物理路：裂隙在、无硬斑，但 **B7 光污染太弱、银河太亮，看着像 B2/B3**，要 substantially 提高。

用户澄清的原则（关键）：**物理上**光污染=天空背景辉光抬高（淹没银河）；**视觉上**人眼自动适应曝光（tone mapping + white-point 归一），所以不该看到"画面越来越亮"，而该看到"银河越来越淡、天空显示亮度大致稳定"。

**踩坑确认**：直接调 `SKYGLOW_SCALE` 是 no-op——银河带亮度 `visual_luminance_for_mags ∝ SKYGLOW_SCALE`，放大 k 倍则银河带和加性辉光同步×k，比值不变，white-point 归一后显示对比一模一样。

**真旋钮** `SKYGLOW_POLLUTION_BOOST`（render_horizon.py，默认 5.0）：只乘到【加性辉光 `add_skyglow` + Weber 阈值 + sky_anchor】，**绝不碰星场/银河带线性亮度**。于是放大它=高 bortle 辉光真正淹过银河，而银河带锚在 B1 不动，共享 white-point 把显示天光底拉回稳定。新增 `additive_skyglow_level()`，sweep 显示链 sat 用 `skyglow_level`（场景锚）、Weber/floor/anchor 用 `additive_skyglow_level`。CLI `--skyglow-pollution-boost` 可覆盖（sweep 只在主进程跑显示链，无 spawn 继承问题）。

**boost=5 全分辨率实测**：显示天光底 B1→B9 大致平（60-65），银河对比 B1=2.44→B3=2.08→B5≈0→B7=0→B9=0 陡降。亲眼核实：B3 银河辉煌、B5 仅银心一抹柔光、B7 近黑只剩星点+银心whisper、全程柔和无硬斑。**用户取舍：保持 boost=5、B7 优先**（接受 B3→B5 曲线偏陡、中档过渡压缩，换 B7 真"被城市吞没"）。`add_skyglow` 改动使 `test_saturation_threshold_rides_magnitude_ladder` 的 floor 扣除改用 `additive_skyglow_level(1)`（保留测试本意：饱和几何对星等阶梯不变）。77 测试通过。

---

## PR2：BSC5 亮星补全（2026-06-11）

Gaia 系统性饱和/漏测最亮星（G≲6）。修法：拉 Yale BSC5（VizieR V/50，astroquery，约 9110 星，完整到 V~6.5），把 Gaia 缺的亮星并进渲染缓存。脚本 `src/merge_bsc5_bright_stars.py`（可复现、幂等，原始 fetch 缓存到 `data/raw/bsc5_raw.npz`，输出 `data/raw/fov_g20_bsc5.npz`，不覆盖 fov_g20.npz）。

**关键发现（改变了 PR2 的预期规模）**：`fov_g20.npz` 是**广州 FOV 预裁的**（b 仅 -41.6..+62.4），不是全天。且 Gaia 的缺口**只在 G<2**——G≥2 处 Gaia 已比 BSC5（V≈6.5 限）更深更全。所以"无脑加 G<6"会重复计入约 1330 颗 Gaia 已有的星；**位置+星等去重（0.05°内、|ΔG|<1.5 则判为 Gaia 已有跳过）后只真缺 20 颗亮星**：Vega(G=0.003)、Antares(0.32)、Altair(0.74)、Shaula(1.59)、Eltanin(1.78) 等。Sirius/Canopus/Betelgeuse/Rigel 等不在这个广州视窗内（天文正确，非缺陷）。

**转换**：① RA/Dec(J2000)→银道 l,b 用 astropy SkyCoord(icrs).galactic。② G←V,B-V 用 Gaia EDR3 Johnson-Cousins 多项式 `G-V=-0.02704+0.01424(B-V)-0.2156(B-V)²+0.01426(B-V)³`。③ bp_rp←B-V 用对 7 颗已知星拟合的线性式 `1.007(B-V)+0.030`（残差<0.07；bp_rp 只驱动颜色不驱动亮度，误差仅 cosmetic）。色彩 sanity：Vega 白(bp_rp 0.03)、Antares 深红(1.87)✓。

**去重内存**：只把 g<7 的 Gaia 子集（~4948 星）拉进内存做球面近邻，不物化 616M。峰值 RSS 12.5G。

**全重渲（用户要一致性，尽管 20 颗在 1080 缩略图上几乎不可见）**：
- bortle 1-9 走 sweep（boost=5、merged cache、T0.04 S0.5），验收 PASS（数字与无 BSC5 时一致，20 颗不动验收）。
- 4K hires `fov_g20_4k.jpg`(2160×3840) + hero `hero_milky_way.jpg`(1000×1778) 同源：Weber-OFF(ext-threshold 0)、bortle 1、psf0.6 高分辨率渲一次。**踩 working.md:205 的坑**（高分辨率比原生 1080 暗一个量级）：用 `--save-linear` 存线性画布、`normalize_panel` 直接重 tone（非 tone_iterate.py，它漏 global stretch），histogram match 到老 noweber 基准（lum 中位 45→46/48、p99 184→169/150，per-channel 近乎逐项对上）。tone：target_sky 0.038 / target_white 2.6。亲眼核实 hero：大裂隙 profound、银心暖金、新增亮星（Vega 蓝白峰229、Antares 黄红峰234）作为视觉锚点显现。
- **eye_grid 仍留 PR3**：B1/B6×敏感度对比，20 颗几乎不影响，且走有 OOM 风险的旧 grid 路径；PR3 一并在物理路重建。

**注意**：`fov_g20_bsc5.npz`(9.2G) 被 `data/raw/` gitignore，不进 git，靠脚本复现。今后渲染默认数据源切到 `fov_g20_bsc5.npz`。

---

## PR3：housekeeping（2026-06-11）

范围克制（刚稳定渲染管线，不碰显示链/数据 build，避免回归）：

- **OOM 护栏**：`render_bortle_eye_grid.py` 串行 `render_grid` 路径（`--workers` 缺省=0 时进入）加大星表拦截——星数 > 50M 直接 SystemExit、指向 `--workers` 并行路径。这正是之前硬死机的 footgun，现彻底堵死。
- **pytest.ini**：`testpaths = tests`，把 `outputs/DSSBackendFrontend/backend/test_app.py`（不相关的 DSO 网络集成测试，默认收集会噪声性 2 failed）排除出主测试。现在 `pytest` 干净 77 passed。
- **eye_grid 重建**：用 BSC5 cache + 并行路径（workers 16）重渲 `bortle_eye_grid`（B1/B6×+0/2/4mag 敏感度对比），落地 2700×3200 与页面一致。从 PR1/PR2 deferred 过来，现完成。
- **文档**：`data_manifest.md` 补 `fov_g20`/`fov_g20_bsc5` 产物条目 + 专门一节"BSC5 亮星补全链"记录下载/合并/坑（VizieR row_limit=-1、FOV 预裁、Gaia 只缺 G<2、位置+星等去重、去重内存、光度转换）。

**有意不做**（survey 提议但判为churn/风险）：
- 不删 `build_render_cache_v3_experimental.py`——data_manifest.md/working.md 明确记录它是**有意保留**的实验路径（深星表裂隙 probing 可能复用），删它违背已记录决策。
- 不抽 `tone_display.py`：把刚稳定的显示链跨 6 文件搬模块，纯组织性重构，回归风险 > 当下收益。留待真要加新渲染器时再做。
- 不合并 cache builder：data build 脚本刚产出 BSC5 cache，不扰动。

---

## principles 页重写为"五个坑" + ablation 全重渲 BSC5 + Weber 对修复（2026-06-11）

**文案 reframe**：principles.html 从"五步流水线"改为"五个坑"叙事（我们犯过的五个错误，每步发现不理想再补规则，终点复现主图）。具体：七百万→六亿(G<20) 订正；颜色坑并进 step1（删独立颜色节）；step4 加数据说明大裂隙就是在此浮现（旧"黑不到照片"遗留问题已由加深星表解决）；step5 文案纠错"Web 预置"→"Weber 对比预置"；不提"两套星表缝合"；去 hero 术语改"主图"；删"两处边界"节，只留"城市夜空能不能纯黑"一处诚实边界。

**step4 交互**：旧五联静图 `ablation_scale_up.jpg` 换成滑块切换器（5 张 `ablation_scale_g13gain/g13/g16/g18/g20.jpg` + inline JS），点按钮换图+caption。

**ablation 全重渲（BSC5 + 主图 tone）**：11 张图全用 BSC5 cache 重渲、对齐夜顶主图提亮 tone（target-sky 0.038/target-white 2.6, Bortle1, boost5, Weber-off）。ablation_5_full ≡ ablation_scale_g20 ≡ 主图（逐像素相同）。命令见 `scripts/render_ablation.sh` + `skills/ablation_study_rendering.md`。删 `ablation_2_psf.jpg`（不再引用）。

**Weber 对修复（关键 bug）**：weber_on/off 两张原用单图 `--bortle 7` 渲，单图路径 sky-floor 不锚物理 skyglow，B7 银河淹不掉、Weber-on 还能看见银河（对比 0.083，错）。**改走 sweep 路径**（`--sweep-bortles 7`，sky_anchor 锚 3*additive_skyglow_level），weber_on 对比 0.000（看不见，对）、weber_off 0.238（band 可见）。亲眼核实 weber_on 近黑只剩星点+银心 whisper。

**视频**：zoom15 用用户 PixInsight 处理过的帧合成带停顿 H.265/hvc1（19s=首停1s+zoom15s+尾停3s），落地 `docs/assets/zoom_milkyway.mp4`（网页也用 H.265，index.html source 加 codecs="hvc1"），poster 更新。

**skills**：新增 `ablation_study_rendering.md`；`hips_1b_tile_generation.md` 加端到端交接流程（agent 渲 tiles → 用户 PixInsight 调色 → 用户说 OK → agent 跑 hipsgen + 改 index.html → 用户 rsync）。

## 杠子接缝 → 全天 tone 标定（PR42-43，2026-06-12，复刻 hero 的完整链）

用户报"暗区沿银河方向的斜杠子"。诊断绕了几轮（误判渐晕、Gaia 扫描、bloom 视场外缺失），
一锤定音判据是**量相邻 tile 重叠区**（同一片天）：raw canvas（accumulate+立体角归一化）块间
比值 1.00 干净，artifact 全在 tone 链。真因：`render_tile` 直接调 `tone_adapted`（单张图自适应，
sky-floor 取本 tile 25 百分位、white-point 取 99.5），含银带多的 tile 标定不同 → 同片天映射到
不同亮度 → 接缝（PR42, #42）。

修复连环揭出三个 bug：
1. **接缝**：改全局固定标定（sky_anchor + 固定 stretch），重叠区 32%→0.1%。
2. **银带过曝**：固定标定移除 per-tile 白点补偿后，CLI 默认 star_contrast=6 把银带冲白；过渡期降到 1.0。
3. **暗空发灰（sky_anchor 量纲）**：anchor 传了 `additive_skyglow_level`（单通道），但 adapt 内部跟
   `canvas.sum(-1)`（三通道）比，差 3 倍 → 黑场抬高 3×、整图发灰。修正 ×3，暗空 p5 64→22。

**最终方案：全天 tone 标定（PR43, #43）**。真根因——hero（render_fov）**无**立体角归一化、
tile（render_tan_wcs）**有**（×REF_OMEGA/cdelt²≈×50），喂进同一 tone 链的 canvas 量级差 50 倍，
所以 hero 的 stretch 数值搬到 tile 必爆。解法 `calibrate_alltile_tone.py`：用与渲染相同 fov/size
**实测**暗空 canvas sum 作 sky_anchor（依赖 norm，render 校验一致），配 hero 同款 star_contrast=4
+ stretch=1.0。render_tan_wcs 加 `--calib`。4K 验证：暗 p5≈26、银心亮 p99≈214 不爆、接缝<1%，
复刻 hero 观感。

## HEALPix 分桶 memory-aware + 高分辨率（PR44，2026-06-12）

要把分辨率从 1B 的 10.5 arcsec/px 压到 zoom video 同款 1.5 arcsec/px（细 7×），全天等效 ~238
亿像素。瓶颈是内存：profile 实测每 tile 对全 6 亿星算 gnomonic 投影 **+30GB/进程**、×8 ~240GB，
且**与 tile-size 无关**（证伪「降 tile-size 省内存」）。根治 `build_healpix_bucketed.py`：星表按
Norder6 像素排序建索引，render_tile 分桶模式用 `cone_search` 只读 tile 邻桶（几万星 vs 6 亿）。
实测渲染**内存零增量（45GB=基线）、快 8×**、结果一致。24 进程 45GB（300G 预算内）。

连带修**亚度文件名碰撞**：tile 名原用 `%+.0f` 整数度，step<1° 时四舍五入同名互相覆盖（361 tile
塌成 132，且并发同名互写让 PI 报 `w undefined`）。改网格索引 `i_j`（hipsgen 靠 .hhh 定位、不靠
文件名）。修后 361→361、PI 0 失败。

## PixInsight shm 段耗尽 + 断点续传（PR45，2026-06-12/13）

batch「卡死一半 worker」的真根因（误判过 slot/WebEngine）：每个 PI 实例占 1 个 SysV shm 段，
macOS 上限 `kern.sysv.shmmni=32`，崩溃实例**泄漏僵尸段**累积占满 → `QSharedMemory::create:
out of resources` 启动即崩。`pixinsight_batch.py` 内置：跑前清无附着 PI 段 + 余量降并发 + 收尾清理。

`--resume` 断点续传：处理过的累积到 `<indir>/.pi_batch_done.log`，--resume 读它跳过、只跑差集
（in-place 必须用，否则双重调色）。实验教训：**mtime 不可靠**（渲染/PI 多进程乱序并发写、交织
无分界，27581 张最大断层仅 10s），done log 才精确。损坏 tile 闭环：零星 `w undefined` 多是渲染
被 kill 时写坏的截断 PNG（PIL 报 truncated），提取 ERR → 从文件名解析 (l,b) 分桶重渲 → --resume
补 PI，最终 done log = 全量（广州高分 27581/27581 0 失败）。

## hipsgen hips_order 限深度（PR45）

1.5 arcsec/px 源不限 order，hipsgen 自动选 Norder9（0.8 arcsec，2× 过采样插值、无新信息、放大
反而糊），瓦片 4×、**12h vs 3.5h**。显式 `hips_order=8`（1.6 arcsec≈源真分辨率）画质不损、省 75%。
（参照 yage.ai/dssv2 旧巡天 properties=hips_order 6 定位此坑。）判据：选 Norder N 的 arcsec/px
最接近源的 N。用户看 Norder9 糊正是这个超采样层——源 tile 本身锐（PSF 无问题）。

## 全天数据 + Linux 远程 hipsgen（PR46，2026-06-13）

扩到真全天（不止广州 FOV）：`build_allsky_manifest.py` 解析 Flatiron 全集目录逐文件字节算真实
增量——全集 688GiB、已有 412（含银心、占大半数据）、**增量仅 276GiB**（非面积外推的 1.9TB）。
`build_fov_deep_cache --all-sky` 跳过 FOV 裁剪读全部 3386 分片 → 全天 npz **10.6 亿星**（b -90~90，
广州版只 -42~62）。

`tools/hipsgen_linux_pkg/`：把最重的 hipsgen 步打包外包给更强 Linux 机器（瓦片渲染/PI 仍本机做，
渲好的 tiles + 自包含包 rsync 过去，那边照 README 跑 hipsgen→HiPS）。hipsgen framing：默认本机、
大规模（全天 ~22 万 tile）可外包。

## 工程教训（这一段沉淀）

- **长程后台任务用 `nohup ... & disown` 脱离会话** + 设 ~30min wakeup 巡检：会话中断会连带 kill
  后台进程且**不发通知**（下载/hipsgen 反复 silently fail 才发现），心跳巡检兜底。
- **删文件用 trash 不用 rm**（pipeline 脚本同理，加 `[ -e ]` 守卫）。
- **产物落 outputs/ 不放 /tmp**（/tmp 会被清、user 接管不了）。
- **每次 merge PR 后切回 master + pull**，本地基线不落后。
- 统计量对 size 不敏感只对**分布形状**成立；sky_anchor 是 canvas **绝对量级**（含 norm），依赖 fov/size。
