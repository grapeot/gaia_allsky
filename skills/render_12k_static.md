# Skill: 12K 单图静态渲染 + 16-bit TIFF 导出

渲一张 12K（6912×12288）广州地平 FOV 银河大图，导出 16-bit TIFF 供后期。
关键技巧：用 `--save-linear` 存线性画布，之后从线性画布秒级重 tone / 调亮 / 出
任意位深，不必重渲 6 亿星。

## 渲 12K + 存线性画布

```bash
source .venv/bin/activate
# wrapper 脚本里跑（nohup 丢 venv）。Bortle 1 +6mag 深空敏感版示例：
python src/render_fov.py --data data/raw/fov_g20_bsc5.npz \
  --out outputs/fov_b1v6_12k_raw.png \
  --bortle 1 --value 6 --faint-gain 1 --workers 16 \
  --width 6912 --height 12288 \
  --save-linear outputs/fov_b1v6_12k_linear.npy
```

- `--value N` = 眼睛敏感度 +N mag（观测者属性，进星场亮度）。+6 = 很灵敏，露出大量暗星。
- `--bortle 1` = 暗空。
- 12K 画布 float64 ≈ 2GB，PSF temp 翻倍 ≈ 4GB，加 616M mmap ≈ 15GB，550GB 机器无压力。
- `--save-linear` 存的是 **Weber + skyglow 后、normalize_panel 前**的线性画布，
  正好可喂 normalize_panel 重 tone。

## 从线性画布重 tone + 出 16-bit TIFF

`tifffile` 已装（PIL 不原生支持 RGB 16-bit）。提亮主要靠 `target_white`：

```python
import numpy as np, sys; sys.path.insert(0,'src')
import render_bortle_eye_grid as beg
import tifffile

lin = np.load('outputs/fov_b1v6_12k_linear.npy')          # 12288×6912×3 float64
# tw2.6 提亮档（target_sky 0.020, target_white 2.6, star_contrast 6, chroma 1.8, gamma 2.2）
f = beg.normalize_sky_adapted(lin, 0.020, 2.2, 99.5, 25.0, 6.0, 2.6, None, 1.8)  # float[0,1]
u16 = (np.clip(f, 0, 1) * 65535.0 + 0.5).astype(np.uint16)
tifffile.imwrite('outputs/fov_b1v6_12k_bright_tw2.6_16bit.tiff', u16,
                 photometric='rgb', compression='deflate')
```

- 出多档提亮先做 720 宽小预览 jpg 给用户挑，挑定再出全分辨率 16-bit TIFF。
- `normalize_sky_adapted` 返回 float[0,1]，×65535 出 16-bit（比 8-bit PNG 多得多的
  暗部/高光层次）；deflate 无损压缩，12K RGB16 约 350M。

## 坑

- **高分辨率比原生 1080 暗一个量级**（信号摊薄到更多像素），naive 降采样救不回。
  若要降采样版，渲完先 plot histogram 和原生 1080 参照对分位，调 tone 对齐再降。
  但 16-bit TIFF 全分辨率成品本身不降采样，直接从线性画布重 tone 即可。
- `tone_iterate.py` 不要用来重 tone 单图——它走 `tone_adapted` 漏了 global signal_stretch，
  和 render_fov 单图链不一致。直接调 `normalize_sky_adapted` / `normalize_panel`。

## 相关文件

- `src/render_fov.py` —— 单图/sweep 渲染
- `outputs/fov_b1v6_12k_linear.npy` —— 当前 12K 线性画布（可反复重 tone）
- `outputs/fov_b1v6_12k_bright_tw2.6_16bit.tiff` —— 当前 12K 成品
