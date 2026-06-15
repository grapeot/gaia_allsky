# Skill: principles 页 ablation study 渲染

`docs/principles.html` 的消融实验：从朴素"一星一像素"出发，每步加一条规则，终点
复现夜顶主图。所有图用 `render_fov.py` 同一条命令、只换参数开关，可复现。

## 核心原则：ablation 终点 = 主图，每步 = 主图减一条规则

主图是 **Bortle 1 暗空、Weber-off、提亮版**。ablation step1-5 全部用这套"主图 tone"，
只逐项打开 psf / faint-gain / sat。Bortle 1 下 Weber 对画面无影响（银河对比远高于阈值），
所以 step5（Weber on）≡ step4（G20），序列到 step4 就到顶。

**主图 tone（step1-5 全用）**，BSC5 cache：
```
--bortle 1 --value 0 --target-sky 0.038 --target-white 2.6 \
--star-contrast 6 --chroma 1.8 --ext-threshold 0 \
--width 1080 --height 1920 --workers 16
```
（boost=5 是 render_horizon 默认，无需传。`--value 0`：不加敏感度增益。）

## 暗空消融阶梯（5 张，data=fov_g20_bsc5）

| 图 | 变动参数 |
|---|---|
| `ablation_1_naive` | `--psf-core-px 0 --faint-gain 1 --sat-over-sky 0` |
| `ablation_3_faintgain` | `--psf-core-px 0.6 --faint-gain 1 --sat-over-sky 0` |
| `ablation_4_satbloom` | `--psf-core-px 0.6 --faint-gain 1 --sat-over-sky 6` |
| `ablation_5_full` | 同 satbloom（= 主图，全规则开，Weber 在 B1 无影响）|

注：用 G<20 真星时 `--faint-gain 1`（不需增益，真星本身够）。naive 在 G<20 下整体
已像银河，但放大看仍是单像素硬颗粒（高频 std 比 full 高约 26%），盐粒感数据上成立。

## STEP 4 加数据切换器（5 张，演示星表深度）

展示数据深度递进，只最深的 G20 含 BSC5 亮星（它就是主图），中间档纯 Gaia 深度：

| 图 | data + 参数 |
|---|---|
| `ablation_scale_g13gain` | `--data fov_g13.npz --faint-gain 3.8`（增益外推奇技淫巧版）|
| `ablation_scale_g13` | `--data fov_g13.npz --faint-gain 1` |
| `ablation_scale_g16` | `--data fov_g16.npz --faint-gain 1` |
| `ablation_scale_g18` | `--data fov_g18.npz --faint-gain 1` |
| `ablation_scale_g20` | `--data fov_g20_bsc5.npz --faint-gain 1`（= 主图）|

其余参数同主图 tone（psf 0.6, sat 6, ext 0）。

## STEP 5 Weber 对比对（2 张，Bortle 7，演示光污染下 Weber 作用）

**这两张不 reproduce 主图**，演示 Weber 阈值在光污染下的效果。**关键坑：必须走
sweep 路径**（`--sweep-bortles 7`），不能用单图 `--bortle 7`。

原因：单图 `main()` 路径的 sky-floor 不锚物理 skyglow，B7 银河淹不掉、Weber-on 还能
看见银河（contrast 0.083，错）。sweep 路径把 sky_anchor 锚到 `3*additive_skyglow_level(b)`，
B7 银河被城市辉光物理淹没，Weber-on contrast=0.000（看不见，对）。用主序列 bortle tone
（target-sky 0.012, target-white 1.0），不用提亮版。

```bash
COMMON="--data data/raw/fov_g20_bsc5.npz --out /tmp/_u.png --faint-gain 1 \
  --target-white 1.0 --workers 16 --scene-ref-bortle 1 --width 1080 --height 1920"
# weber_on: 银河该看不见
python src/render_fov.py $COMMON --ext-threshold 0.04 --ext-softness 0.5 \
  --sweep-bortles 7 --sweep-out-dir /tmp/_w_on
# weber_off: 相机能看到的残留银河
python src/render_fov.py $COMMON --ext-threshold 0 \
  --sweep-bortles 7 --sweep-out-dir /tmp/_w_off
```
落地：`/tmp/_w_on/bortle_7.png → ablation_weber_on.jpg`，`_w_off → ablation_weber_off.jpg`。

验收：weber_on band/sky 对比 ≈0.000（看不见）、weber_off ≈0.24（band 可见）。差别明显才对。

## 通用

- 全部 1080×1920，无烧入标签（页面 CSS 控制显示），PNG → JPG q90 落 `docs/assets/`。
- wrapper 脚本内 `source .venv/bin/activate`，**串行**渲（一次一个 python，--workers 16），
  616M cache 每次 mmap 加载一次，峰值 RSS ~15GB。
- PNG→JPG 用 `magick`（`cjpeg` 不读 PNG）。
- 验收：`ablation_scale_g20` 与 `ablation_5_full` 应逐像素近似相同（都=主图）。

## 相关文件

- `src/render_fov.py` —— 渲染器
- `docs/principles.html` —— 消融页（引用这 11 张图 + step4 切换器 JS）
- `scripts/render_ablation.sh` —— 暗空阶梯 + 切换器的批渲脚本（weber 对单独走 sweep）
