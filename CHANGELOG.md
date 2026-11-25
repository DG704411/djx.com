# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.2] - 2026-11-25

### 修复

- **零依赖运行器在 Linux 上整轮崩溃**：`tests/run_tests.py` 遇到「跳过」时直接挂掉，
  导致 CI 的 `Smoke test` 步骤在 ubuntu 上失败（Windows 恰好不走跳过分支所以没事）。
  根因是 `_skip()` 调用了 `pytest.skip()`，它抛的 `Skipped` 继承自 `BaseException`
  而不是 `Exception`，运行器接不住。现在 `_skip()` 只抛自家的 `SkipTest`
  （pytest 那侧本来就由 `@windows_only` 这类 mark 提前跳过），运行器另外把
  `Skipped/XFailed/XPassed` 也识别为跳过，作为兜底
- **Windows runner 上测试全过却判失败**：13 个用例都 PASS，但打印中文汇总时抛
  `UnicodeEncodeError`（GitHub 的 Windows runner 用 cp1252 解码 stdout，
  编不出「通过 / 跳过 / 失败」）。现在运行器强制把 stdout/stderr 切到 UTF-8，
  CI 里另外加了 `PYTHONIOENCODING=utf-8` 和 `python -X utf8` 双保险

## [1.0.1] - 2026-11-25

### 修复

- **CI 全线飘红**：`ruff` 静态检查拦下了全部 4 个 matrix 任务，测试因此从未执行。
  修正 4 处违规：`typing.Callable/Iterable` → `collections.abc`、多余的
  `.encode("utf-8")`、多余的 `open(..., "r")` 模式参数、`gui.py` 导入排序
- **CI 拆分 `lint` 与 `test` 两个 job**：lint 失败不再连带跳过测试，
  并改用 `--output-format=github` 把违规直接标注到文件行上
- **固定 `ruff==0.17.6`**：ruff 每版都会调整规则集，浮动版本会让 CI 无故变红

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
