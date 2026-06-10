# 工作记录

## Changelog

### 2026-06-09

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

- 银河乳光和尘埃暗带是同一份信息的正负两面：都来自暗星计数。给 G9-11 暗星乘截断补偿增益（光度函数外推合理区间 4-7），乳光和暗带同时出现，不需要单独的 density mask 或宽 PSF 辉光层。增益的物理含义是让观测到的暗星代理 G>11 不可分辨族群。

- 缩略图里银河断裂的问题应在输出层解决（线性光域先降采样再 gamma 编码），不要为此在渲染层把暗星预先糊化。

- `--target-sky` 设为 0.03（不是 0.12），背景估计用低百分位（`--sky-pct 25`）。全图中位数会被大面积银河拉高，导致暗天面板发灰。

- `outputs/` 已在 gitignore 中，帧目录（`--frames-dir`）和最终 mp4/png 均落在此目录下。帧目录是独立输出，ffmpeg 只在帧写完后运行，避免帧数据通过 IPC 传回主进程。
