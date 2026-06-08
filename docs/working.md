# 工作记录

## Changelog

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

- 输出网格在 UI 中缩小后，1px 星点会导致银河/星场空洞。必须用双层 PSF：锐利点源层（`--point-psf-px 1.0`）加宽扩散层（`--psf-px 6.0 --diffuse-strength`）。

- `--target-sky` 设为 0.03（不是 0.12），背景估计用低百分位（`--sky-pct 25`）。全图中位数会被大面积银河拉高，导致暗天面板发灰。

- `outputs/` 已在 gitignore 中，帧目录（`--frames-dir`）和最终 mp4/png 均落在此目录下。帧目录是独立输出，ffmpeg 只在帧写完后运行，避免帧数据通过 IPC 传回主进程。
