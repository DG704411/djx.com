"""GitPusher 核心逻辑：GitHub API + git 流水线。

刻意不含任何界面代码，方便无界面测试（见 selftest.py）。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

API_BASE = "https://api.github.com"
USER_AGENT = "GitPusher/1.0"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# GitHub 单文件硬限制 100MB，超过直接推不上去
FILE_HARD_LIMIT = 100 * 1024 * 1024
FILE_WARN_LIMIT = 45 * 1024 * 1024
TOTAL_WARN_LIMIT = 900 * 1024 * 1024

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

DEFAULT_GITIGNORE = """\
# ---- GitPusher 默认忽略项（可自由修改）----
__pycache__/
*.py[cod]
.venv/
venv/
env/
node_modules/
dist/
build/
target/
.idea/
.vscode/
*.log
.DS_Store
Thumbs.db
*.tmp
"""

PHASE_ERRORS = {
    401: "Token 无效或已过期。请重新生成一个 Token（需要勾选 repo 权限）。",
    403: "权限不足或触发频率限制。请确认 Token 勾选了 repo 权限，或稍后再试。",
    404: "找不到该仓库。请检查『仓库名』是否写对，或改用『创建新仓库』。",
    422: "仓库名已存在或名称不合法。",
}


class GitHubError(RuntimeError):
    """给用户看的错误（消息本身就是中文提示）。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class ScanResult:
    file_count: int = 0
    total_bytes: int = 0
    big_files: list[tuple[str, int]] = field(default_factory=list)
    oversize_files: list[tuple[str, int]] = field(default_factory=list)

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)


@dataclass
class SyncParams:
    folder: Path
    repo: str
    token: str
    login: str = ""              # 可留空，自动取 /user
    mode: str = "create"         # create | existing
    private: bool = True
    description: str = ""
    branch: str = "main"
    message: str = ""
    force: bool = False
    auto_merge: bool = True      # 推送前/被拒时自动合并远端


# --------------------------------------------------------------- HTTP 层
def api_request(method: str, path: str, token: str | None = None,
                payload: dict | None = None, timeout: int = 30) -> tuple[int, dict]:
    url = API_BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token.strip()}")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, _parse_json(body)
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, _parse_json(body)
    except urlerror.URLError as exc:
        raise GitHubError(
            f"连不上 GitHub API（{exc.reason}）。请检查网络或代理设置。") from exc
    except TimeoutError as exc:
        raise GitHubError("连接 GitHub API 超时，请检查网络。") from exc


def _parse_json(body: str) -> dict:
    body = (body or "").strip()
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except ValueError:
        return {"message": body[:400]}


def whoami(token: str) -> dict:
    """验证 Token 并返回用户信息。"""
    if not token or not token.strip():
        raise GitHubError("请先填入 Token。")
    status, data = api_request("GET", "/user", token)
    if status != 200:
        raise GitHubError(_api_message(status, data), status)
    return data


def repo_exists(token: str, owner: str, name: str) -> bool:
    status, _ = api_request("GET", f"/repos/{owner}/{name}", token)
    return status == 200


def create_repo(token: str, name: str, private: bool, description: str = "") -> dict:
    payload = {
        "name": name,
        "private": bool(private),
        "description": description or "",
        "has_issues": True,
        "has_wiki": False,
        "auto_init": False,  # 空仓库，第一次 push 才不会冲突
    }
    status, data = api_request("POST", "/user/repos", token, payload)
    if status == 201:
        return data
    raise GitHubError(_api_message(status, data), status)


def _api_message(status: int, data: dict) -> str:
    raw = str(data.get("message") or "").strip()
    detail = ""
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        parts = []
        for item in errors:
            if isinstance(item, dict):
                parts.append(str(item.get("message") or item.get("code") or item))
            else:
                parts.append(str(item))
        detail = "；".join(parts)
    base = PHASE_ERRORS.get(status)
    if base:
        return base + (f"（GitHub 回复：{raw}{' / ' + detail if detail else ''}）" if raw else "")
    return f"GitHub 返回 {status}：{raw or '未知错误'}{(' / ' + detail) if detail else ''}"


# ----------------------------------------------------------------- git 层
def _git_env() -> dict:
    env = dict(os.environ)
    # 坚决不让 git 弹窗要密码：否则界面会卡死
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = "echo"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def git(args: Iterable[str], cwd: Path | str, timeout: int = 900) -> tuple[int, str, str]:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=_git_env(), creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError as exc:
        raise GitHubError(
            "找不到 git 命令。请先安装 Git for Windows（https://git-scm.com/download/win）。") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubError(f"git 命令超时（{timeout}s）：git {' '.join(args)}") from exc
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def git_version() -> str:
    code, out, err = git(["--version"], Path.home())
    if code != 0:
        raise GitHubError(f"git 不可用：{err or out}")
    return out


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def has_commits(path: Path) -> bool:
    code, out, _ = git(["rev-parse", "--verify", "HEAD"], path)
    return code == 0 and bool(out)


def current_remote(path: Path) -> str:
    code, out, _ = git(["remote", "get-url", "origin"], path)
    return out if code == 0 else ""


def branch_name(path: Path) -> str:
    code, out, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    return out if code == 0 and out and out != "HEAD" else ""


def is_dirty(path: Path) -> bool:
    code, out, _ = git(["status", "--porcelain"], path)
    return code == 0 and bool(out.strip())


# -------------------------------------------------------------- 本地体检
_IGNORED_SCAN_DIRS = {".git"}


def scan_folder(root: Path) -> ScanResult:
    """统计文件数量/体积，找出会被 GitHub 拒绝的巨型文件。"""
    result = ScanResult()
    root = Path(root)
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except (PermissionError, OSError):
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _IGNORED_SCAN_DIRS:
                        continue
                    stack.append(Path(entry.path))
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except (PermissionError, OSError):
                continue
            rel = os.path.relpath(entry.path, root)
            result.file_count += 1
            result.total_bytes += size
            if size >= FILE_HARD_LIMIT:
                result.oversize_files.append((rel, size))
            elif size >= FILE_WARN_LIMIT:
                result.big_files.append((rel, size))
    result.oversize_files.sort(key=lambda x: -x[1])
    result.big_files.sort(key=lambda x: -x[1])
    return result


def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def sanitize_repo_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\s]+", "-", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-._")
    return name[:100]


def validate_repo_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise GitHubError("请填写仓库名。")
    if not REPO_NAME_RE.match(name):
        raise GitHubError(
            "仓库名只能用英文字母、数字、点、下划线、连字符，长度 1-100。")
    return name


def ensure_gitignore(root: Path, log: Callable[[str], None]) -> None:
    target = root / ".gitignore"
    if target.exists():
        return
    try:
        target.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
        log("已创建 .gitignore（忽略 __pycache__/node_modules/venv 等），可自行增删。")
    except OSError as exc:
        log(f"（.gitignore 创建失败，忽略：{exc}）")


# ------------------------------------------------------- 提交 & 推送流程
def ensure_identity(root: Path, login: str, log: Callable[[str], None]) -> None:
    """缺 user.name/user.email 时，只给本仓库设局部身份，不动全局配置。"""
    name = git(["config", "user.name"], root)[1]
    email = git(["config", "user.email"], root)[1]
    if name and email:
        return
    fallback_name = name or login or "GitHub User"
    fallback_email = email or f"{login or 'user'}@users.noreply.github.com"
    if not name:
        git(["config", "user.name", fallback_name], root)
    if not email:
        git(["config", "user.email", fallback_email], root)
    log(f"已为本仓库设置提交身份：{fallback_name} <{fallback_email}>")


def init_repo(root: Path, branch: str, log: Callable[[str], None]) -> None:
    code, out, err = git(["init", "-b", branch], root)
    if code != 0:
        # 老版本 git 不支持 -b
        code2, _, err2 = git(["init"], root)
        if code2 != 0:
            raise GitHubError(f"git init 失败：{err or err2}")
        git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], root)
    log(f"已初始化本地 git 仓库（分支 {branch}）。")


def commit_all(root: Path, message: str, log: Callable[[str], None]) -> bool:
    code, out, err = git(["add", "-A"], root)
    if code != 0:
        raise GitHubError(f"git add 失败：{err or out}")
    if not is_dirty(root):
        log("没有需要提交的改动。")
        return False
    code, out, err = git(["commit", "-m", message], root)
    if code != 0:
        raise GitHubError(f"git commit 失败：{err or out}")
    log(f"已提交：{message}")
    return True


def _auth_args(token: str) -> list[str]:
    """一次性认证头；Token 只出现在本次命令行参数里，不落盘。"""
    pair = base64.b64encode(
        f"x-access-token:{token.strip()}".encode("utf-8")).decode("ascii")
    return ["-c", "credential.helper=",
            "-c", f"http.extraHeader=Authorization: Basic {pair}"]


def _remote_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


_REJECTION_MARKERS = ("non-fast-forward", "fetch first", "[rejected]",
                      "failed to push some refs")


def _is_rejection(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _REJECTION_MARKERS)


def _merge(root: Path, url: str, branch: str, auth_args: list[str],
           log: Callable[[str], None]) -> str:
    """把远端 branch 的提交接到本地 HEAD 之后。

    绝不 force、绝不丢本地提交；rebase 前先打备份分支，失败自动中止并还原。
    返回 "noop"（无需合并）/ "ff"（快进）/ "rebased"（重放到远端之后）。
    """
    log("检查远端是否有新提交…")
    code, out, err = git([*auth_args, "ls-remote", url, f"refs/heads/{branch}"], root)
    if code != 0:
        raise GitHubError(f"读取远端分支失败：{err or out}")
    if not out.strip():
        log("远端还没有这个分支（首次推送），无需合并。")
        return "noop"

    code, local_sha, _ = git(["rev-parse", "HEAD"], root)
    if code != 0 or not local_sha:
        raise GitHubError("本地还没有任何提交，无法合并远端。")

    code, out, err = git([*auth_args, "fetch", "--no-tags", url, branch], root)
    if code != 0:
        raise GitHubError(f"拉取远端失败：{err or out}")
    code, fetched, _ = git(["rev-parse", "FETCH_HEAD"], root)
    if code != 0 or not fetched:
        raise GitHubError("无法解析远端提交。")

    if fetched == local_sha:
        log("远端与本地一致，无需合并。")
        return "noop"
    if git(["merge-base", "--is-ancestor", fetched, "HEAD"], root)[0] == 0:
        log("本地已包含远端全部提交，无需合并。")
        return "noop"
    if git(["merge-base", "--is-ancestor", "HEAD", fetched], root)[0] == 0:
        code, out, err = git(["merge", "--ff-only", "FETCH_HEAD"], root)
        if code != 0:
            raise GitHubError(f"快进合并失败：{err or out}")
        log(f"本地落后于远端，已快进到 {fetched[:8]}。")
        return "ff"

    backup = "gitpusher-backup-" + time.strftime("%Y%m%d-%H%M%S")
    git(["branch", "-f", backup, "HEAD"], root)
    log(f"远端有分叉内容，正在把本地提交接到远端之后…（本地已备份为分支 {backup}）")
    code, out, err = git(["rebase", "FETCH_HEAD"], root)
    if code != 0:
        git(["rebase", "--abort"], root)
        raise GitHubError(
            "远端改动与本地冲突，自动合并没成功（本地已还原，没有丢任何东西）。\n"
            "可选：① 勾选『强制覆盖远端』再上传（会覆盖远端内容）；"
            "② 手动处理冲突后重试。\n"
            f"git 输出：{(err or out)[-600:]}")
    log("已合并远端改动。")
    return "rebased"


def merge_remote(root: Path, owner: str, repo: str, token: str, branch: str,
                 log: Callable[[str], None]) -> str:
    """只把远端更新合并到本地，不推送。"""
    return _merge(root, _remote_url(owner, repo), branch, _auth_args(token), log)


def push_to_github(root: Path, owner: str, repo: str, token: str,
                   branch: str, force: bool, log: Callable[[str], None],
                   auto_merge: bool = True) -> str:
    """推送到 GitHub。被拒且 auto_merge 时，自动合并远端后重试一次。"""
    clean_url = _remote_url(owner, repo)
    existing = current_remote(root)
    if existing:
        if existing.rstrip("/") != clean_url.rstrip("/"):
            git(["remote", "set-url", "origin", clean_url], root)
            log(f"origin 已改为 {clean_url}")
    else:
        git(["remote", "add", "origin", clean_url], root)
        log(f"已设置 origin = {clean_url}")

    args = [*_auth_args(token), "push", "-u"]
    if force:
        args.append("--force")
        log("⚠ 使用强制推送（--force），远端历史可能被覆盖。")
    args += ["origin", f"HEAD:refs/heads/{branch}"]

    def _do_push() -> tuple[int, str]:
        log(f"正在推送到 {clean_url} ...")
        code, out, err = git(args, root)
        return code, "\n".join(part for part in (out, err) if part)

    code, combined = _do_push()
    if code != 0 and auto_merge and _is_rejection(combined):
        log("推送被拒绝：远端已有新提交。自动合并远端后重试…")
        _merge(root, clean_url, branch, _auth_args(token), log)
        code, combined = _do_push()
    if code != 0:
        raise GitHubError(_explain_push_failure(combined), None)
    return f"https://github.com/{owner}/{repo}"


def _explain_push_failure(text: str) -> str:
    lowered = text.lower()
    if _is_rejection(text):
        return ("推送被拒绝：GitHub 上的仓库里已经有内容，而且自动合并也没能成功。\n"
                "解决办法：① 勾选『强制覆盖远端』再点一次（会覆盖远端旧内容）；"
                "或 ② 先点『从 GitHub 拉取更新』把远端改动合进本地，再上传。")
    if "authentication failed" in lowered or "invalid username or password" in lowered \
            or "403" in lowered or "401" in lowered:
        return "推送认证失败：Token 无效，或没有该仓库的写入权限（repo 权限）。"
    if "large files detected" in lowered or "exceeds github's file size limit" in lowered:
        return "推送失败：有文件超过 GitHub 的 100MB 单文件上限，请先移出这些文件。"
    if "could not resolve host" in lowered or "failed to connect" in lowered \
            or "timeout" in lowered:
        return "推送失败：连不上 github.com，请检查网络或代理。"
    return "推送失败：\n" + text[-1500:]


# --------------------------------------------------------------- 总流程
def sync_job(p: SyncParams, log: Callable[[str], None]) -> dict:
    """完整流程：体检 → 建/验仓库 → 初始化 → 提交 → 推送。

    所有失败都抛 GitHubError（消息可直接展示给用户）。
    """
    folder = Path(p.folder).expanduser()
    if not folder.is_dir():
        raise GitHubError(f"文件夹不存在：{folder}")
    repo = validate_repo_name(p.repo)

    log(f"git 版本：{git_version()}")

    scan = scan_folder(folder)
    if scan.file_count == 0:
        raise GitHubError("这个文件夹是空的，没有代码可以上传。")
    log(f"扫描完成：{scan.file_count} 个文件，共 {human_size(scan.total_bytes)}。")
    if scan.oversize_files:
        listing = "\n".join(f"  · {rel}  {human_size(size)}"
                            for rel, size in scan.oversize_files[:10])
        raise GitHubError(
            "以下文件超过 GitHub 单文件 100MB 上限，推不上去：\n" + listing +
            "\n请先移出或加入 .gitignore，再重试。")
    if scan.big_files:
        listing = "\n".join(f"  · {rel}  {human_size(size)}"
                            for rel, size in scan.big_files[:10])
        log("提醒：这些文件偏大（>45MB），推送会很慢：\n" + listing)
    if scan.total_bytes > TOTAL_WARN_LIMIT:
        log(f"提醒：仓库体积 {human_size(scan.total_bytes)} 偏大，推荐用 Git LFS。")

    user = whoami(p.token)
    login = p.login or str(user.get("login") or "")
    if not login:
        raise GitHubError("无法获取 GitHub 用户名，请检查 Token。")
    log(f"已登录 GitHub：{login}（Token 有效）")

    if p.mode == "create":
        if repo_exists(p.token, login, repo):
            raise GitHubError(
                f"仓库 {login}/{repo} 已经存在。请把模式改成『推送到已有仓库』。")
        info = create_repo(p.token, repo, p.private, p.description)
        log(f"已在 GitHub 创建仓库：{info.get('full_name') or login + '/' + repo}"
            f"（{'私有' if p.private else '公开'}）")
    else:
        if not repo_exists(p.token, login, repo):
            raise GitHubError(
                f"仓库 {login}/{repo} 不存在。请检查名字，或改用『创建新仓库』。")
        log(f"目标仓库已存在：{login}/{repo}")

    if not is_git_repo(folder):
        init_repo(folder, p.branch, log)
    else:
        log("检测到本地已有 git 仓库，沿用现有历史。")

    ensure_gitignore(folder, log)
    ensure_identity(folder, login, log)

    message = (p.message or "").strip() or f"更新 {time.strftime('%Y-%m-%d %H:%M')}"
    committed = commit_all(folder, message, log)
    if not committed and not has_commits(folder):
        raise GitHubError("没有可提交的内容。")

    if p.auto_merge and p.mode == "existing":
        merge_remote(folder, login, repo, p.token, p.branch, log)

    url = push_to_github(folder, login, repo, p.token, p.branch, p.force, log,
                         auto_merge=p.auto_merge)
    log("✅ 上传完成。")
    return {
        "login": login,
        "repo": repo,
        "url": url,
        "files": scan.file_count,
        "bytes": scan.total_bytes,
        "committed": committed,
    }
