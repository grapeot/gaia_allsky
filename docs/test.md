# 测试策略

所有测试在 `tests/test_render.py`，一个文件，四十多个用例，分三组：不需要数据的自动数学测试、需要本地 Gaia 缓存的条件集成测试、手工视觉验证。

## 默认测试命令

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

这个命令跑全部自动测试。没有 `.venv` 的话，先用 `uv venv` 创建并 `uv pip install -r requirements.txt`。

## 自动化覆盖范围

所有 test 都构造已知输入、检查已知输出，验证规则和边界。

- **星等亮度**：5 等等差 → 100 倍亮度差（Pogson 公式）；m_ref 锚点归一为 1。
- **星色**：B-V < 0 的蓝白星蓝色分量 > 红色；B-V > 1.4 的橙红星反之。
- **投影**：Mollweide 银心落在画面中心，mask 为椭圆；equirectangular 经纬度单调性正确。
- **3D 重投影**：近星飞近变亮、飞远变暗（平方反比）；北斗七星飞 300pc 后 RA 散布减半（星座散架）；5000pc 远星方向几乎不变（银河是大尺度结构）。
- **Bortle skyglow**：辉光等级单调递增（1 到 9）；高污染区辉光远强于暗区（淹没银河）。
- **NELM/Bortle 对照**：增益系数每 1 等约 2.512 倍；经验 NELM 表（Bortle 1 → 7.8，Bortle 6 → 5.3）与图面标签一致；有效 NELM 处星光对比度为预设值。
- **tone mapping 和显示映射**：暗部压缩 log > gamma > linear；HDR 输出为 uint16 不越界；全黑画布不崩溃；sky floor 归一化让不同面板背景对齐；共享 reference stretch 避免各 panel 独立拉满；白点百分位拉伸信号而非停在中灰；star_contrast 参数正确提升背景以上信号。
- **共享运动轨迹**：VR 和前向版共享当前默认位置路径，先朝北斗方向短距离移动，再飞向银心方向上方目标。旧 `l_trajectory` / `motion.l_motion` 的单元测试仍检查 L 两段正交（沿银道 ⊥ 垂直银道）和轨迹连续性。
- **视频 CLI**：VR 用 equirectangular 2:1 分辨率；duration × fps 自动计算帧数；前向版默认先朝北斗飞再转向银心上方，相机 look_dirs 均为单位向量；北斗 overlay 点在第一帧画面内。
- **Gaia 缓存 schema**：全天 fetcher 查询生成 l/b/g/bp_rp 字段；缺失 BP-RP 用太阳型颜色 fallback，不产生 NaN。
- **集成行为**：透视图填充矩形而非鱼眼圆盘；地平相机把地平线放在图像下缘；默认广州视角银心上中天高度合理。

## 依赖本地 Gaia 缓存的测试

`test_milky_way_emerges_density` 检查银道面恒星密度显著高于高银纬区域，验证银河从真实星表密度中涌现。它需要 `data/raw/gaia_g8.npz`。如果文件不存在，pytest 自动 skip。获取方法：

```bash
python src/fetch_gaia_allsky.py --gmax 8 --output data/raw/gaia_g8.npz
```

## 手工验证清单

自动测试检查数学和 CLI 语义，不能替代视觉判断。正式发布前，建议逐项检查以下输出：

| 文件 | 检查要点 |
|---|---|
| `outputs/knob_bortle_eye_grid.png` | Bortle 1 / +4mag 是最亮参考，其他格不过曝 |
| `outputs/knob_bortle_scale_grid.png` | Bortle 1 银河可见，Bortle 7-9 基本暗下去 |
| `outputs/vr_equirect_hires.mp4` | 4096×2048，60fps，600 frames，转向平滑 |
| `outputs/big_dipper_forward_hires.mp4` | 2160×2160，60fps，600 frames，北斗连线可见 |

## 发布前检查

公开发布前至少跑完：

```bash
python -m pytest tests/ -q
git status --short
```

最后做 privacy review：逐条确认 Git 工作树和历史里没有个人路径、邮箱、token、私有域名、1Password 引用或大二进制文件。`data/raw/` 和 `outputs/` 已被 `.gitignore` 排除，但仍需检查是否有文件被意外 `git add -f`。
