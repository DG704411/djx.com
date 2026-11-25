"""GitPusher 测试套件。

两种跑法，结果等价：

    python -m pytest -v        # 需要 pytest
    python tests/run_tests.py  # 零依赖运行器（本机没装 pytest 时用这个）

标了 ``network`` 的用例需要访问 api.github.com / github.com，且**只使用假 Token**，
预期就是认证失败，不会写入任何真实仓库。
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

try:  # pytest 是可选依赖：没装也要能跑零依赖运行器
    import pytest
except ModuleNotFoundError:  # pragma: no cover
    pytest = None

# src 布局：未安装本包时也能直接导入
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gitpusher import core  # noqa: E402
from gitpusher import secure_store as store  # noqa: E402


# ----------------------------------------------------------------- 小工具
class SkipTest(Exception):
    """零依赖运行器识别的跳过信号。"""


def _skip(reason: str) -> None:
    if pytest is not None:
        pytest.skip(reason)
    raise SkipTest(reason)


network = pytest.mark.network if pytest is not None else (lambda fn: fn)
windows_only = (
    pytest.mark.skipif(os.name != "nt", reason="DPAPI 仅 Windows 可用")
    if pytest is not None
    else (lambda fn: fn)
)

_TEMP_DIRS: list[Path] = []


def mkdtemp(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(path)
    return path


def force_rmtree(path: Path) -> bool:
    """删目录。git 对象文件是只读的，Windows 上要先去掉只读位再删。"""
    def _on_error(func, target, _exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onerror=_on_error)
    except OSError:
        pass
    return not path.exists()


def cleanup_temp_dirs() -> tuple[int, list[str]]:
    cleaned = sum(1 for tmp in _TEMP_DIRS if force_rmtree(tmp))
    left = [tmp.name for tmp in _TEMP_DIRS if tmp.exists()]
    _TEMP_DIRS.clear()
    return cleaned, left


if pytest is not None:  # pragma: no cover
    @pytest.fixture(scope="session", autouse=True)
    def _cleanup_temp_dirs():
        yield
        cleanup_temp_dirs()


def _bare(tmp: Path) -> Path:
    bare = tmp / "remote.git"
    code, out, err = core.git(["init", "--bare", "-b", "main", str(bare)], tmp)
    assert code == 0, err
    return bare


def _commit_all(repo: Path, message: str) -> None:
    ident = ["-c", "user.name=gittest", "-c", "user.email=gittest@example.com"]
    core.git(["add", "-A"], repo)
    code, out, err = core.git([*ident, "commit", "-m", message], repo)
    assert code == 0, err or out


# ------------------------------------------------------------------- 环境
def test_git_available():
    """git 必须存在，且版本号能读到。"""
    version = core.git_version()
    assert version.startswith("git version")


# --------------------------------------------------------------- 加密存储
@windows_only
def test_dpapi_roundtrip():
    """Token 走 DPAPI 加密往返，密文里不许出现明文。"""
    if os.name != "nt":
        _skip("DPAPI 仅 Windows 可用")
    secret = "ghp_TESTtoken1234567890_中文"
    blob = store.encrypt_secret(secret)
    assert blob.startswith("dpapi:")
    assert "ghp_TESTtoken" not in blob, "密文里出现了明文！"
    assert store.decrypt_secret(blob) == secret


def test_config_roundtrip():
    """配置文件读写、忘记 Token、目录自动创建。"""
    tmp = mkdtemp("gitpusher_cfg_")
    old_dir, old_path = store.APP_DIR, store.CONFIG_PATH
    store.APP_DIR, store.CONFIG_PATH = tmp, tmp / "config.json"
    try:
        store.save_config({"token": store.encrypt_secret("ghp_abc"), "last_repo": "demo"})
        cfg = store.load_config()
        assert store.decrypt_secret(cfg["token"]) == "ghp_abc"
        assert cfg["last_repo"] == "demo"
        assert (tmp / "config.json").exists()
        store.forget_token()
        assert "token" not in store.load_config()
    finally:
        store.APP_DIR, store.CONFIG_PATH = old_dir, old_path


# ------------------------------------------------------------------ API
@network
def test_api_bad_token_is_401():
    """假 Token 必须被判成 401，并翻译成人话。"""
    status, data = core.api_request("GET", "/user", "ghp_this_token_is_fake_000000")
    assert status == 401
    assert "Bad credentials" in str(data.get("message", ""))
    assert core.PHASE_ERRORS[401]


@network
def test_api_unauthenticated_ok():
    """未鉴权请求也能连通（用 /zen 探活）。"""
    status, data = core.api_request("GET", "/zen", None)
    assert status == 200
    assert data


# ------------------------------------------------------------------ 体检
def test_scan_folder_and_limits():
    """统计文件数与体积；.git 不计入；>100MB 进黑名单。"""
    tmp = mkdtemp("gitpusher_scan_")
    (tmp / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "b.txt").write_text("x" * 1000, encoding="utf-8")
    (tmp / ".git").mkdir()
    (tmp / ".git" / "huge").write_bytes(b"0" * 5000)
    with open(tmp / "big.bin", "wb") as fh:  # 稀疏文件，秒建 101MB
        fh.seek(101 * 1024 * 1024 - 1)
        fh.write(b"\0")

    scan = core.scan_folder(tmp)
    assert scan.file_count == 3
    assert len(scan.oversize_files) == 1
    assert not any(rel.startswith(".git") for rel, _ in scan.oversize_files)
    assert core.human_size(scan.total_bytes)


def test_repo_name_rules():
    """仓库名清洗与校验。"""
    assert core.sanitize_repo_name("my proj!") == "my-proj"
    assert core.sanitize_repo_name("hello world") == "hello-world"
    assert core.sanitize_repo_name("my_proj-1.2") == "my_proj-1.2"
    for bad in ("bad name!", "", "中文名"):
        try:
            core.validate_repo_name(bad)
        except core.GitHubError:
            continue
        raise AssertionError(f"非法仓库名未被拦截：{bad!r}")


def test_empty_folder_rejected():
    """空文件夹必须被拦下，而不是建一个空仓库。"""
    tmp = mkdtemp("gitpusher_empty_")
    params = core.SyncParams(folder=tmp, repo="x", token="dummy")
    try:
        core.sync_job(params, lambda _msg: None)
    except core.GitHubError as exc:
        assert "空" in str(exc)
    else:
        raise AssertionError("空文件夹未被拦截")


# --------------------------------------------------------------- git 流水线
@network
def test_commit_and_push_failure():
    """真实 git 提交；假 Token 推送必须快速失败，且 Token 不落盘。"""
    tmp = mkdtemp("gitpusher_git_")
    (tmp / "main.py").write_text("print('hello')\n", encoding="utf-8")
    log = lambda _m: None  # noqa: E731

    core.init_repo(tmp, "main", log)
    core.ensure_gitignore(tmp, log)
    core.ensure_identity(tmp, "gittest-user", log)
    assert core.commit_all(tmp, "自测提交", log) is True
    assert core.has_commits(tmp)
    assert not core.is_dirty(tmp)
    assert (tmp / ".gitignore").exists()

    started = time.time()
    try:
        core.push_to_github(tmp, "gitpusher-selftest-owner", "no-such-repo",
                            "ghp_fake_token_000", "main", False, log)
    except core.GitHubError as exc:
        elapsed = time.time() - started
        assert elapsed < 120, f"报错耗时 {elapsed:.1f}s，可能卡在交互提示"
        assert "认证" in str(exc) or "推送" in str(exc)
    else:
        raise AssertionError("假 Token 竟然推送成功？")

    config_text = (tmp / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    assert "ghp_fake_token" not in config_text, "Token 被写进了 .git/config！"
    assert "x-access-token" not in config_text


# ------------------------------------------------------------------ 合并
def test_merge_unrelated_histories():
    """真实场景：远端已有 unrelated 的 Initial commit，本地是新历史。"""
    tmp = mkdtemp("gitpusher_merge_unrel_")
    bare = _bare(tmp)

    other = tmp / "other"
    other.mkdir()
    core.git(["init", "-b", "main"], other)
    (other / "README.md").write_text("# remote readme\n", encoding="utf-8")
    _commit_all(other, "Initial commit")
    assert core.git(["push", str(bare), "HEAD:refs/heads/main"], other)[0] == 0

    local = tmp / "local"
    local.mkdir()
    (local / "main.py").write_text("print('hi')\n", encoding="utf-8")
    core.init_repo(local, "main", lambda _m: None)
    core.ensure_identity(local, "gittest", lambda _m: None)
    core.commit_all(local, "我们的第一个提交", lambda _m: None)

    state = core._merge(local, str(bare), "main", [], lambda _m: None)
    assert state == "rebased"
    assert (local / "README.md").exists(), "远端文件没合并进来"
    assert (local / "main.py").exists(), "本地文件被弄丢了！"
    assert core.git(["push", str(bare), "HEAD:refs/heads/main"], local)[0] == 0
    assert core.git(["branch", "--list", "gitpusher-backup-*"], local)[1].strip(), "没有备份分支"


def test_merge_fast_forward():
    """本地落后远端时应快进，不产生新提交。"""
    tmp = mkdtemp("gitpusher_merge_ff_")
    bare = _bare(tmp)
    work = tmp / "work"
    work.mkdir()
    core.git(["init", "-b", "main"], work)
    (work / "a.txt").write_text("1", encoding="utf-8")
    _commit_all(work, "a")
    assert core.git(["push", str(bare), "HEAD:refs/heads/main"], work)[0] == 0

    local = tmp / "local"
    local.mkdir()
    core.git(["init", "-b", "main"], local)
    core.git(["fetch", "--no-tags", str(bare), "main"], local)
    core.git(["reset", "--hard", "FETCH_HEAD"], local)
    before = core.git(["rev-parse", "HEAD"], local)[1]

    (work / "b.txt").write_text("2", encoding="utf-8")
    _commit_all(work, "b")
    assert core.git(["push", str(bare), "HEAD:refs/heads/main"], work)[0] == 0

    state = core._merge(local, str(bare), "main", [], lambda _m: None)
    after = core.git(["rev-parse", "HEAD"], local)[1]
    assert state == "ff"
    assert (local / "b.txt").exists(), "远端新文件没拉下来"
    assert before != after


def test_merge_noop():
    """已同步时不该动历史。"""
    tmp = mkdtemp("gitpusher_merge_noop_")
    bare = _bare(tmp)
    work = tmp / "work"
    work.mkdir()
    core.git(["init", "-b", "main"], work)
    (work / "a.txt").write_text("1", encoding="utf-8")
    _commit_all(work, "a")
    assert core.git(["push", str(bare), "HEAD:refs/heads/main"], work)[0] == 0

    state = core._merge(work, str(bare), "main", [], lambda _m: None)
    assert state == "noop"


def test_detect_push_rejection():
    """真拒绝能识别；认证/权限错误不能误判成拒绝。"""
    real = (" ! [rejected]        main -> main (non-fast-forward)\n"
            "error: failed to push some refs to 'https://github.com/x/y.git'\n"
            "hint: Updates were rejected because the tip of your current branch is behind")
    assert core._is_rejection(real) is True
    assert core._is_rejection("Everything up-to-date") is False
    assert core._is_rejection("fatal: Authentication failed for 'https://...'") is False
    assert core._is_rejection("remote: Permission denied") is False
