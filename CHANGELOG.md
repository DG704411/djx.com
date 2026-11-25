# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-11-25

首个可发布版本。

### 新增

- **桌面界面**（tkinter）：选文件夹 → 填仓库名 → 点一下就把代码提交并推送到 GitHub
- **命令行版**：`python -m gitpusher.cli`，支持脚本化、批量和无界面环境
- **三种 Token 来源**：`--token` > 环境变量 `GITHUB_TOKEN` > 界面里加密保存的
- **推送前自动合并远端**：先 `ls-remote` → `fetch` → 判断祖先关系 → 需要时 `rebase`，
  让「仓库里已经有 README/Initial commit」不再是拦路虎
- **推送被拒时自动重试**：识别 `non-fast-forward` 后自动合并再推一次
- **「从 GitHub 拉取更新」**：只拉不推，支持快进 / 本地提交接到远端之后
- **合并前自动备份**：建立 `gitpusher-backup-<时间戳>` 分支；冲突自动 `--abort` 还原
- **上传前体检**：统计文件数与体积，单文件超 100MB（GitHub 硬上限）直接拦下，>45MB 提醒
- **自动 `.gitignore`**：仅当项目里不存在时生成，忽略 `__pycache__`/`node_modules`/`venv` 等
- **Token 加密存储**：Windows DPAPI，配置文件权限收紧到仅当前用户
- **13 项自动化测试**：涵盖加密往返、API 鉴权、体检拦截、git 流水线、三种合并场景

### 修复

- 自测会在 `%TEMP%` 留下临时目录：现在跑完自动清理
- Windows 上含 `.git` 的临时目录删不掉（git 对象文件是只读的，`shutil.rmtree` 静默失败）：
  改为先清除只读位再删，并且只在真正删掉后才计数

### 安全

- Token 不写入 `.git/config`、不进 credential store、不进日志
- 所有 git 子进程强制 `GIT_TERMINAL_PROMPT=0`，不会卡在密码交互上
- 提交身份只写入目标仓库的局部配置，不修改全局 git 配置
