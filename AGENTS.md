# Gaia 全天星空实验 - Agent 指南

这个仓库目标是公开发布。默认所有新增文件都要按 public GitHub 仓库标准处理。

## 工作原则

- 代码和文档默认写中文，必要的 API/CLI 名称保留英文。
- 不要提交 `data/raw/`、`outputs/`、`.venv/`、`__pycache__/` 或任何本地缓存。
- 大结果图、视频和网页压缩资产要分清：完整渲染缓存不进 Git；GitHub Pages 只放压缩后的展示资产。
- 重要设计决策同步到 `docs/rfc.md`；迭代调参记录可写入 `docs/working.md`。
- 修改测试策略时同步更新 `docs/test.md`。

## 验证

默认验证命令：

```bash
source .venv/bin/activate
python -m pytest tests/ -q
```

如果没有 `.venv`，先用 `uv venv` 创建，再用 `uv pip install -r requirements.txt` 安装依赖。

## 发布前检查

- 跑完整测试。
- 扫描当前工作树和 Git 历史，确认没有个人路径、邮箱、token、私有域名、1Password 引用和大二进制文件进入 Git。
- 确认 README 里的命令可被新 agent 直接执行。
