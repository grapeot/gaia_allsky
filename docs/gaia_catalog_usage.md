# Gaia 星表使用指南

本文记录本项目使用 Gaia DR3 星表的本地数据约定。目标是让后续 agent 不必重新摸索：哪些文件已经在本机，哪些文件是派生缓存，什么场景该走 Gaia Archive ADQL，什么场景该走 Flatiron bulk mirror。

`data/raw/` 当前实际有哪些文件、每个是活是死、能不能删，见同目录的 `data_manifest.md` 台账。本文讲"约定与流程"，台账讲"硬盘现状"。

## 数据目录

所有原始星表和派生缓存都放在：

```text
data/raw/
```

这个目录被 `.gitignore` 整体忽略，不提交 Git。`outputs/` 也只放渲染输出，同样不提交。

当前重要文件分三类。

第一类是渲染器直接读取的 NPZ 缓存：

```text
data/raw/gaia_g13.npz          # 全天 G<13, 字段 l,b,g,bp_rp, 约 225 MB
data/raw/gaia_g13_ag.npz       # 全天 G<13 + ag_gspphot, 字段 l,b,g,bp_rp,ag
data/raw/gaia_g13_render.npz   # 正式静态图缓存, 字段 l,b,g,bp_rp,proxy_atten
data/raw/gaia_3d_deep_g13.npz  # 3D 飞行缓存, 字段 ra,dec,parallax,dist_pc,g,bp_rp
```

第二类是历史分段缓存。它们来自 Gaia Archive ADQL 分段查询，用来绕过匿名 300 万行截断：

```text
data/raw/gaia_g11.npz
data/raw/gaia_g11_12.npz
data/raw/gaia_g12_125.npz
data/raw/gaia_g125_128.npz
data/raw/gaia_g128_13.npz
```

第三类是 Flatiron bulk 原始 gzip，目前用于广州银心 FOV 的更深星表实验：

```text
data/raw/flatiron_gaia_source_fov_gz/
  GaiaSource_*.csv.gz   # Flatiron GaiaSource 原始 ECSV gzip 分片
  manifest.csv          # 本次选择的分片清单：文件名、URL、HEALPix range、字节数
  urls.txt              # aria2 下载 URL 列表
  summary.txt           # FOV 参数和总规模摘要
  download.log          # aria2 下载日志
  download.pid          # 下载进程 PID；下载完成后该进程会退出
```

这批原始 gzip 是从 Flatiron Gaia DR3 mirror 下载的广州银心视场相关分片。当前清单规模为 2044 个文件，压缩后总量约 411.91 GiB。下载完成后，所有文件都应有对应的完整 `.csv.gz`，目录里不应残留 `.aria2` partial 文件。

## 何时用哪条通道

小规模查询、冒烟测试、按星等拿到 G<13 这种百万行量级数据，可以用 `src/fetch_gaia_allsky.py` 走 Gaia Archive ADQL。注意 Gaia Archive 匿名查询有 3,000,000 行硬上限，超限会无声截断。任何结果行数恰好等于 3,000,000 都应视为失败，需要继续细分星等区间。

更深星表、区域星表、G<16/G<18/G<20 这类千万到十亿行量级任务，应走 Flatiron bulk mirror，不要走 ADQL/TAP。TAP 是查询服务，不适合作为批量下载通道。

Flatiron 入口：

```text
https://sdsc-users.flatironinstitute.org/~gaia/dr3/csv/GaiaSource/
```

Flatiron 的 `GaiaSource` 文件是 Gaia DR3 `gaiadr3.gaia_source` 的 ECSV gzip。文件按 HEALPix level 8 的连续 index range 切分，文件名形如：

```text
GaiaSource_131011-132722.csv.gz
```

这里的 `131011-132722` 是该文件覆盖的 nested HEALPix level-8 index 范围。文件内部不是按星等排序，也不是只含某个星等范围。因此要做 `G<16` 时，需要先下载覆盖目标天区的原始 gzip，再在本地过滤 `phot_g_mean_mag < 16`。

## CSV/ECSV 格式

Flatiron CSV 文件实际是 ECSV：开头约 1000 行以 `#` 开头的 metadata，之后第一条非注释行是 CSV header，再之后是数据行。

对当前 2D 静态渲染，最小需要列是：

```text
l, b, phot_g_mean_mag, bp_rp
```

保存到本项目 NPZ 时字段名约定为：

```text
l, b, g, bp_rp
```

其中 `l/b` 是银道坐标，`g` 是 Gaia G 星等，`bp_rp` 用于星色。`bp_rp` 缺失时，现有 fetcher 用 `0.7` 填充。

如果后续需要逐星消光或 3D 筛选，可额外保留：

```text
ag_gspphot, parallax, parallax_over_error, ra, dec
```

但正式静态图不需要完整 GaiaSource 的几十上百列。原始 gzip 可以长期留在本机，派生缓存则应尽量只保留渲染器需要的列。

## 当前广州银心 FOV

正式静态图默认使用广州纬度、银心中天附近的竖幅地平视角。相关默认值在 `src/render_bortle_eye_grid.py`：

```text
lat_deg = 23.13
lst_hours = 17.76
projection = horizon_window
panel_width = 1080
panel_height = 1920
az_width_deg = 90.0
max_alt_deg = 75.0
fov_axis = horizontal
look_az = None  # 自动对准银心方位角，约 180°
```

代码里的 `horizon_window` 实际调用 `project_horizon_camera()`，它是 rectilinear 透视相机：水平 FOV 固定为 90°，垂直 FOV 由画幅比例推导。默认 1080×1920 时，有效垂直 FOV 约 121.28°，相机中心高度约 60.64°，底部中心射线落在地平线。

现有 G<13 全天缓存实测，这个视场包含：

```text
G<13 inside: 2,436,898 / 7,369,627 = 33.07%
```

几何面积约占全天二成，但因为视场正对银心，星数占比高于面积占比。

## FOV 到 HEALPix 分片

Flatiron 文件已经按 HEALPix level 8 range 切好。要找某个 FOV 需要下载哪些原始 gzip，流程是：

第一，读取 Flatiron 目录页，解析全部 `GaiaSource_<lo>-<hi>.csv.gz` 及文件大小。

第二，用 `astropy-healpix` 枚举 nested level-8 HEALPix 的中心点。Gaia DR3 source_id 的 HEALPix 约定也基于 level 8；Flatiron 文件名里的范围就是这个 level-8 index。

第三，把每个 HEALPix 中心点从 ICRS 转成 Galactic，再用项目现有坐标链投影到广州地平相机，判断它是否落入画面。

第四，选择任何一个 inside HEALPix index 与文件 `[lo, hi]` range 相交的 gzip 文件。

下面是生成当前广州银心 FOV 下载清单的最小代码骨架。它依赖 `astropy-healpix`，如环境没有，先运行 `uv pip install astropy-healpix`。

```python
import re
import urllib.request
from pathlib import Path

import numpy as np
from astropy_healpix import HEALPix
from astropy.coordinates import ICRS, SkyCoord

import render_horizon as rh
import render_bortle_eye_grid as beg

base = "https://sdsc-users.flatironinstitute.org/~gaia/dr3/csv/GaiaSource/"
html = urllib.request.urlopen(base, timeout=60).read().decode("utf-8", "replace")
pat = re.compile(
    r'data-order="(GaiaSource_(\d{6})-(\d{6})\.csv\.gz)".*?'
    r'<td data-order="(\d+)">',
    re.S,
)
files = []
for m in pat.finditer(html):
    name = m.group(1)
    lo = int(m.group(2))
    hi = int(m.group(3))
    size_bytes = int(m.group(4))
    files.append((lo, hi, size_bytes, name, base + name))

lat_deg = 23.13
lst_hours = 17.76
width = 1080
height = 1920
h_fov_deg = 90.0
v_ref_deg = 75.0
fov_axis = "horizontal"

center_az, _ = beg.galactic_center_altaz(lat_deg, lst_hours)

nside = 2 ** 8
npix = 12 * nside * nside
hp = HEALPix(nside=nside, order="nested", frame=ICRS())
idx = np.arange(npix)
lon, lat = hp.healpix_to_lonlat(idx)
coord = SkyCoord(ra=lon, dec=lat, frame="icrs")
gal = coord.galactic

az, alt = rh.gal_to_altaz(gal.l.deg, gal.b.deg, lat_deg, lst_hours)
_, _, inside = beg.project_horizon_camera(
    az, alt, center_az, width, height, h_fov_deg, v_ref_deg, fov_axis
)

selected = []
for lo, hi, size_bytes, name, url in files:
    if inside[lo:hi + 1].any():
        selected.append((lo, hi, size_bytes, name, url))

print(len(selected), sum(row[2] for row in selected) / 1024**3, "GiB")
```

当前广州银心 FOV 结果是：

```text
selected_files = 2044
selected_gib = 411.91
```

这个筛选按 HEALPix 中心点判断文件是否相交。它比精确多边形相交更简单，且 Flatiron 文件本身是连续 range，边界会自然带入一些 padding。对于本项目这种只想减少全表下载量的场景，这个精度足够。

## 下载方式

如果已有 `urls.txt`，推荐用 `aria2c` 断点续传：

```bash
aria2c \
  --input-file="data/raw/flatiron_gaia_source_fov_gz/urls.txt" \
  --dir="data/raw/flatiron_gaia_source_fov_gz" \
  --continue=true \
  --max-concurrent-downloads=8 \
  --split=1 \
  --file-allocation=none \
  --auto-file-renaming=false \
  --allow-overwrite=false
```

`--split=1` 是有意的：每个 gzip 约 200 MB，同时下 8 个文件已经能跑满常见网络；单文件多连接对镜像站更重，也没有必要。下载中会产生 `.aria2` partial 控制文件。下载完成后，应满足：

```text
complete_files == manifest.csv 行数
aria2_partials == 0
```

## 本地过滤思路

当前没有必要为了原始 gzip 写复杂 CLI。它们就是 ECSV gzip，可以用 Python 标准库流式读取，也可以用 pandas/pyarrow 做更快的列裁剪。

过滤 `G<16` + 当前 FOV 的核心逻辑是：

第一，逐个打开 `GaiaSource_*.csv.gz`。

第二，跳过开头以 `#` 开头的 ECSV metadata，读取 CSV header。

第三，只取 `l,b,phot_g_mean_mag,bp_rp` 四列。

第四，先筛 `phot_g_mean_mag < 16`，再调用：

```python
az, alt = rh.gal_to_altaz(l, b, lat_deg, lst_hours)
_, _, inside = beg.project_horizon_camera(
    az, alt, center_az, width, height, h_fov_deg, v_ref_deg, fov_axis
)
```

第五，把 inside 的行保存为 shard，最后合并为 NPZ 或保留 Parquet shards。

如果输出 NPZ，字段仍用：

```text
l, b, g, bp_rp
```

如果输出 Parquet，建议字段名保持 Gaia 原名或同时写 manifest。渲染器当前直接读 NPZ，因此最省事的终态仍是 NPZ；数据很大时，先写多个 NPZ shard，再合并或改渲染器支持 shard 读取。

## 注意事项

不要把 `data/raw/` 或 `outputs/` 里的大文件加入 Git。

不要用 Gaia Archive ADQL 拉 G<16/G<20 全天或大 FOV 数据；它会排队、超时或被 300 万行上限截断。

不要假设 Flatiron 文件已经按星等过滤。bulk 文件只按天空位置切，任何 `G<16` 都是本地 filter。

不要把 `bp_rp` 当 Johnson B-V。代码里的 `bv_to_rgb()` 函数名是历史遗留，实际输入和标定都按 Gaia BP-RP 处理。

如果要复现当前下载清单，优先使用 `data/raw/flatiron_gaia_source_fov_gz/manifest.csv` 和 `summary.txt`，而不是重新猜 FOV 参数。
