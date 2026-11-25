# 安全说明

这个工具会在本机保存你的 GitHub 访问令牌，并代表你创建仓库、推送代码。下面是它做的和**不**做的。

## 它如何保管 Token

- 用 **Windows DPAPI**（`CryptProtectData`）加密后写入 `%LOCALAPPDATA%\GitPusher\config.json`
- 密文只能被**当前 Windows 用户账户**解开；把文件拷到别的电脑或别的账户都解不开
- 配置文件权限收紧为「仅当前用户」（`icacls /inheritance:r /grant:r <user>:F`）
- 你可以随时取消勾选「记住 Token」——下次保存会把它从磁盘删掉

## 它如何传递 Token

- 只出现在推送时的一次性 `git -c http.extraHeader=...` 参数里
- **不会**写进 `.git/config`，**不会**写进 git credential store，**不会**出现在日志里
- 所有 git 子进程都设置了 `GIT_TERMINAL_PROMPT=0`，不会挂起等你输密码

## 它对仓库做了什么

一次上传依次是：验证 Token → 创建空仓库 → 扫描文件（>100MB 直接拒绝）→ 写 `.gitignore`（仅当不存在）→ `git init/add/commit` → 可选合并远端 → `git push`。

**不会**做的事：不使用 `--force`（除非你显式勾选）、不删除远端文件、不修改你的全局 git 配置、不读取仓库以外的文件。

合并远端前一定会先建 `gitpusher-backup-<时间戳>` 备份分支；rebase 冲突会自动 `--abort` 还原。

## 报告漏洞

请不要开公开 issue。用 GitHub 的 [私密漏洞报告](https://github.com/DG704411/gitpusher/security/advisories/new)，
或在 issue 里只描述"我想报告安全问题，请给我一个私下沟通的渠道"。

## 如果你的 Token 泄露了

立刻去 <https://github.com/settings/tokens> 删掉它。删掉后本工具会提示「Token 无效」，重新生成一个粘进去即可，其它数据不受影响。
