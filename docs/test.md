# 测试策略

这个项目的测试分两层：自动测试（保证数学和 CLI 不出错）和手工验证（保证图片看起来对）。

## 默认测试

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

当前测试覆盖星等亮度、星色、投影、3D 重投影、Bortle skyglow、NELM 语义、tone mapping、共享运动轨迹、视频 CLI 配置、Gaia 缓存 schema 和部分集成行为。

## 不依赖大数据的测试

大多数测试只使用小数组构造输入，不需要 Gaia 大星表。这些测试用于保证数学关系和 CLI 语义稳定。

重点包括：

- 5 等星等差对应 100 倍亮度差。
- B-V 色指数映射出蓝白星和红星差异。
- Mollweide、equirectangular、地平透视投影位置合理。
- 3D 近星飞近变亮、飞远变暗。
- Bortle skyglow 随等级单调增加。
- Bortle/NELM 表和 panel 标签一致。
- 视觉图使用 shared reference stretch，而不是每个 panel 独立拉满。
- VR 和 forward 视频共享位置轨迹。

## 依赖本地 Gaia 缓存的测试

`test_milky_way_emerges_density` 需要 `data/raw/gaia_g8.npz`。如果文件不存在，pytest 会自动 skip。这个测试检查银道面恒星密度显著高于高银纬区域，用于验证银河从星表密度中涌现。

## 手工验证

图像和视频仍需要手工检查。自动测试只能保证几何、物理关系和 CLI 行为，不能完全判断视觉表达是否适合科普展示。

正式发布前建议检查：

- `outputs/knob_bortle_eye_grid.png`：Bortle 1/+4mag 是最亮 reference，其他格不过曝。
- `outputs/knob_bortle_scale_grid.png`：Bortle 1 能看到银河，Bortle 7-9 基本暗下去。
- `outputs/vr_equirect_hires.mp4`：4096x2048，60fps，600 frames。
- `outputs/big_dipper_forward_hires.mp4`：2160x2160，60fps，600 frames，北斗连线可见。

## 发布前验证

公开发布前至少完成：

```bash
python -m pytest tests/ -q
git status --short
```

还要做 privacy review，确认没有个人路径、邮箱、token、私有域名、大型数据缓存或完整输出进入 Git 历史。
