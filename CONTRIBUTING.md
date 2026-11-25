# 参与贡献

欢迎提 Issue 和 PR。

## 开发环境

需要 Python 3.10+ 与 Git。运行时**零第三方依赖**，只有开发/测试需要额外装包：

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .[dev]
```

## 本地跑测试

两种方式，结果等价：

```bat
rem 方式一：零依赖运行器（本机没装 pytest 也能跑）
python tests\run_tests.py

rem 方式二：pytest
python -m pytest -v
```

其中标了 `network` 的部分需要访问 `api.github.com` 与 `github.com`；
它们只使用**假 Token**，不会真的往任何仓库推东西（预期就是认证失败）。

## 代码风格

```bat
python -m ruff check .
python -m ruff format --check .
```

- 行宽 100，目标 Python 3.10
- 面向用户的错误信息用中文，代码标识符与注释用英文/中文均可
- 新增的 git 操作一律设置 `GIT_TERMINAL_PROMPT=0`，禁止交互式等待

## 提交信息

用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
feat: 支持从 GitHub 拉取更新
fix: 修复 Windows 上临时目录删不掉
docs: 补充 Token 权限说明
```

## 安全红线

- **不要**在任何测试、示例、日志里出现真实 Token
- **不要**给推送流程加隐式 `--force`
- **不要**把 Token 写进 `.git/config` 或 credential store
- 改动安全相关行为时，请同步更新 `SECURITY.md` 与 `CHANGELOG.md`
