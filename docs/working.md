# 工作记录

## Changelog

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

**根因（已确认）**：在 G<13 这个深度、广州地平视角这个取景下，缝方向 Gaia 记录的星就是这么多这么亮，主要是前景星和透过缝的较亮星，全是真的。缝的消光只有约 2 等，不足以把这些前景/较亮星压没。实拍里几十倍的对比来自长曝光能看到 G<13 远达不到的暗星，在那个深度上缝后面所有星被消光压没，缝才真黑——这是我们的数据深度造不出来的。

**立场结论**：用 Gaia DR3 + "只画真实星、不叠尘埃贴图"的诚实渲染原则，这条缝就到现在这个程度，这是真实数据深度的物理边界。要突破要么花大代价加深星表（判决实验证明无效），要么破立项原则显式叠加三维尘埃消光外推。决定接受物理边界，在原理页/文章里诚实写明"缝的对比受限于 Gaia DR3 深度，是真实数据的诚实呈现"，不追求逼近照片。

**副产物（与裂隙无关但有价值）**：PSF 诊断图里"无增益 + 窄 PSF（0.6）"那一格星点更锐利、整体不糊，视觉上更耐看。已另起实验用该参数重渲 Bortle 1，评估是否值得作为新默认。
