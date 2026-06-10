# data/raw 文件台账

本文件是 `data/raw/` 的清单与分类。整个目录被 `.gitignore` 忽略，不进 Git；本台账记录每个文件**是什么、被谁引用、是源还是产物、能否重建**，避免后续 agent（或自己）面对一堆平铺的 npz 分不清死活。

使用约定的总说明见 `gaia_catalog_usage.md`（同目录）。本台账只负责"现在硬盘上有什么、能不能删"。

最后更新：2026-06-10。盘点口径：`du -m`（npz 单位 MB）。

## 分类图例

- **产物**：渲染器/视频管线直接读取的最终缓存。删了要重新 build，且 build 依赖下面的"源"。
- **源**：被某个 build/merge 脚本读取、用来生成"产物"的输入。删了产物就无法重建（除非重新下载）。
- **实验源**：只被实验版脚本引用（非正式管线）。当前正式渲染不读，但保留以便复跑实验。
- **孤儿**：没有任何代码或文档引用，早期实验的中间产物。删除最安全。

## 渲染器最终读取的产物（活，勿删）

| 文件 | MB | 被谁读 | 说明 |
|---|---|---|---|
| `gaia_g13_render.npz` | 254 | `render_bortle_eye_grid.py` DATA_DEFAULT | 正式静态图缓存，字段 l,b,g,bp_rp,proxy_atten。由 `build_render_cache.py` 从 ag 5 段生成 |
| `gaia_3d_deep_g13.npz` | 328 | `video_common.py` DATA_DEFAULT；`derive_video_gain_g13.py` | VR/前向视频的 3D 星表，字段 ra,dec,parallax,dist_pc,g,bp_rp |
| `gaia_3d_deep.npz` | 57 | `render_l_video.py` | 旧 L 轨迹视频的 3D 星表（旧路线，较少用） |
| `gaia_g8.npz` | 2 | `test_render.py`（存在才跑，否则 skip） | 银河涌现密度验证测试数据，docs/test.md 有获取命令 |

## 正式产物的源（活，勿删——删了产物无法重建）

`gaia_g13_render.npz` 的构建链：5 个 ag 分段 → `build_render_cache.py` → render 缓存。

| 文件 | MB | 角色 |
|---|---|---|
| `gaia_ag_0_11.npz` | 48 | `build_render_cache.py` SEGMENTS[0] |
| `gaia_ag_11_12.npz` | 71 | SEGMENTS[1] |
| `gaia_ag_12_12p5.npz` | 66 | SEGMENTS[2] |
| `gaia_ag_12p5_12p8.npz` | 55 | SEGMENTS[3] |
| `gaia_ag_12p8_13.npz` | 45 | SEGMENTS[4] |
| `gaia_g13_ag.npz` | 254 | 上述 5 段的合并体（全天 G<13 + ag_gspphot） |

## 历史分段（活但低频——README 记录的 G<13 合并链）

`gaia_g13.npz`（无 ag 版）的来源分段。`gaia_g13.npz` 本身仍在，分段保留用于增量更新/轻量测试。

| 文件 | MB | 角色 |
|---|---|---|
| `gaia_g13.npz` | 225 | 全天 G<13，字段 l,b,g,bp_rp（无 ag） |
| `gaia_g11.npz` | 29 | `fetch_gaia_allsky.py` 默认输出；G<13 合并源 |
| `gaia_g11_12.npz` | 57 | G<13 合并源（11–12 段） |
| `gaia_g12_125.npz` | 53 | G<13 合并源（12–12.5 段） |
| `gaia_g125_128.npz` | 44 | G<13 合并源（12.5–12.8 段） |
| `gaia_g128_13.npz` | 36 | G<13 合并源（12.8–13 段） |

## 实验源（保留——只被实验版脚本读）

`gaia_full_*` 5 段是 `build_render_cache_v3_experimental.py` 的 SEGMENTS（3D 距离壳层消光实验，v3）。正式渲染走 v2（`build_render_cache.py`，读 ag 段），不读这 5 段。working.md 记录 v3 是有意保留的"增益解读分叉"实验路径，且 2026-06-10 启动的深星表裂隙 probing 可能复用这条线，故保留。

| 文件 | MB |
|---|---|
| `gaia_full_0_11.npz` | 58 |
| `gaia_full_11_12.npz` | 85 |
| `gaia_full_12_12p5.npz` | 79 |
| `gaia_full_12p5_12p8.npz` | 66 |
| `gaia_full_12p8_13.npz` | 53 |

## 孤儿（无任何引用——删除最安全，约 280 MB）

grep 全 src/tests/docs 无引用。早期 3D+ag 合并实验的中间产物，最终 `gaia_3d_deep_g13.npz` 未采用 ag 列（见 rfc 的"已知不对称"）。**保留与否不影响任何现有流程**；若需腾空间，这 6 个文件可直接 trash。

| 文件 | MB |
|---|---|
| `gaia_3dag_0_11.npz` | 47 |
| `gaia_3dag_11_12.npz` | 69 |
| `gaia_3dag_12_12p5.npz` | 64 |
| `gaia_3dag_12p5_12p8.npz` | 53 |
| `gaia_3dag_12p8_13.npz` | 43 |
| `gaia_3d.npz` | 9 |

## Flatiron 深星表原始分片（412G，慎动）

| 路径 | 规模 | 说明 |
|---|---|---|
| `flatiron_gaia_source_fov_gz/` | 412G，2044 个 `.csv.gz` | 广州银心 FOV 的完整 GaiaSource 原始 ECSV gzip，**未按星等过滤**（每片含到 ~21 等的全深度星）。用于 G<16/18/20 的深星表裂隙实验 |

下载已完成：2044 个 .csv.gz 全到齐（== manifest.csv 的 2044 行），0 个 `.aria2` partial。目录里的 `download.pid` 是已退出进程的残留文件，不代表下载仍在进行。重下/校验用 `manifest.csv` + `summary.txt`，不要重新猜 FOV 参数。详细使用流程见 `gaia_catalog_usage.md`（同目录）。

## 能不能删？速查

- 想腾空间且零风险：删"孤儿"6 个（约 280 MB）。
- 任何"产物""源""历史分段""实验源"：**不要删**，删了要么破坏渲染缓存重建，要么得重新下载。
- 412G flatiron：深星表实验做完、确认不再需要前不要删；重下成本是数百 GB 的网络传输。
