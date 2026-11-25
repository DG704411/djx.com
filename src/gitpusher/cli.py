"""命令行版：不用界面也能推代码。

用法：
  python -m gitpusher.cli --folder D:\\mycode --repo my-project
  python -m gitpusher.cli --folder . --repo my-project --public --message "首次提交"
  python -m gitpusher.cli --folder . --repo my-project --existing --force

Token 来源优先级：--token > 环境变量 GITHUB_TOKEN > 本地加密配置（GUI 里保存过的）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import core
from . import secure_store as store


def main() -> int:
    ap = argparse.ArgumentParser(description="把本地文件夹推送到 GitHub 仓库")
    ap.add_argument("--folder", required=True, help="本地文件夹")
    ap.add_argument("--repo", required=True, help="GitHub 仓库名")
    ap.add_argument("--token", default="", help="GitHub PAT（不填则用环境变量或已保存的）")
    ap.add_argument("--mode", choices=["create", "existing"], default="create")
    ap.add_argument("--public", action="store_true", help="默认创建私有仓库，加此项改为公开")
    ap.add_argument("--description", default="")
    ap.add_argument("--message", default="")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--force", action="store_true", help="强制推送（覆盖远端）")
    ap.add_argument("--no-auto-merge", action="store_true",
                    help="推送前/被拒时不自动合并远端（默认会自动合并）")
    ap.add_argument("--pull-only", action="store_true",
                    help="只把远端更新合并到本地，不上传")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = ap.parse_args()

    token = (args.token or os.environ.get("GITHUB_TOKEN")
             or store.decrypt_secret(store.load_config().get("token", "")))
    if not token:
        print("找不到 Token：请用 --token 传入，或先打开 GitPusher.pyw 保存一次。", file=sys.stderr)
        return 2

    def log(msg: str) -> None:
        print(msg, flush=True)

    if args.pull_only:
        try:
            login = str(core.whoami(token).get("login") or "")
            log(f"已登录 GitHub：{login}")
            state = core.merge_remote(Path(args.folder), login, args.repo, token,
                                      args.branch, log)
        except core.GitHubError as exc:
            print(f"失败：{exc}", file=sys.stderr)
            return 1
        log({"noop": "本地已是最新。", "ff": "已快进到远端最新。",
             "rebased": "已把本地提交接到远端之后。"}.get(state, "完成。"))
        return 0

    params = core.SyncParams(
        folder=Path(args.folder), repo=args.repo, token=token,
        mode=args.mode, private=not args.public, description=args.description,
        message=args.message, branch=args.branch, force=args.force,
        auto_merge=not args.no_auto_merge)

    if not args.yes:
        target = f"{args.folder} → https://github.com/<你>/{args.repo}"
        print(f"即将上传：{target}（{'私有' if params.private else '公开'}）")
        if input("确认继续？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消。")
            return 1

    try:
        result = core.sync_job(params, log)
    except core.GitHubError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    print(f"完成：{result['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
