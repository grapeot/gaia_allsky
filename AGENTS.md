# AGENTS.md — AI 编程助手工作指南

全仓库工作语言：中文。API 名称、CLI 参数、变量名保留英文。

## Public Repo 约束

以下内容**严禁提交 Git**：

- `data/raw/` — 原始星表缓存
- `outputs/` — 完整渲染输出（PNG/TIFF/EXR/MP4）
- `.venv/` — 虚拟环境
- `.env` — 环境变量（仅 `.env.example` 可提交）
- `__pycache__/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/` — Python 缓存
- 完整渲染缓存、大二进制文件、个人路径、邮箱、token

GitHub Pages 只放压缩后的展示图。

## 文档同步规则

每次修改代码或参数，必须同步对应的文档：

| 变更类型 | 需同步的文件 |
|---------|------------|
| 修改物理模型、渲染管线、数据流 | `docs/rfc.md` |
| 修改科学目标、成功标准 | `docs/prd.md` |
| 修改测试策略、覆盖范围 | `docs/test.md` |
| 迭代调参、实验记录 | `docs/working.md` |
| 修改 CLI 入口、安装步骤、复现命令 | `README.md` |
| 修改 GitHub Pages 展示资产 | `docs/index.html` |
| 修改 Bortle/NELM 参考数据 | `docs/bortle_skyglow.md` |

## 环境与验证

首次环境搭建：

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

日常验证：

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

测试不需要完整星表缓存，缺缓存时集成测试会自动 skip。

## 发布前 Privacy Review

- 跑完整测试。
- 扫描工作树和 Git 历史，确认没有个人路径、邮箱、token、私有域名、1Password 引用、大二进制文件混入。
- 确认 `README.md` 里的命令能在新环境里直接跑通。
