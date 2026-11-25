#!/usr/bin/env python
"""代码上传助手 · GitHub —— 桌面版（双击运行，无需浏览器）

把本地文件夹一键提交并推送到 GitHub 仓库。
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import core
from . import secure_store as store

TOKEN_URL = ("https://github.com/settings/tokens/new"
             "?scopes=repo&description=GitPusher-%E4%BB%A3%E7%A0%81%E4%B8%8A%E4%BC%A0%E5%8A%A9%E6%89%8B")
APP_TITLE = "代码上传助手 · GitHub"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x780")
        self.minsize(760, 700)

        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.last_url = ""
        self.login = ""

        cfg = store.load_config()
        self.var_token = tk.StringVar(value=store.decrypt_secret(cfg.get("token", "")))
        self.var_folder = tk.StringVar(value=cfg.get("last_folder", ""))
        self.var_repo = tk.StringVar(value=cfg.get("last_repo", ""))
        self.var_private = tk.BooleanVar(value=bool(cfg.get("private", True)))
        self.var_remember = tk.BooleanVar(value=bool(cfg.get("remember", True)))
        self.var_mode = tk.StringVar(value=cfg.get("mode", "create"))
        self.var_desc = tk.StringVar(value="")
        self.var_message = tk.StringVar(value="")
        self.var_force = tk.BooleanVar(value=False)
        self.var_auto_merge = tk.BooleanVar(value=bool(cfg.get("auto_merge", True)))
        self.var_show = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="就绪")
        self.var_login = tk.StringVar(value="未登录")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._drain_queue)
        if self.var_token.get().strip():
            self.after(400, self.verify_token_async)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ttk.Label(head, textvariable=self.var_login, foreground="#2f6f45").pack(side="right")

        ttk.Label(outer, foreground="#666",
                  text="选一个本地文件夹 → 填仓库名 → 点『开始上传』。代码会提交并推送到你的 GitHub。"
                  ).pack(fill="x", pady=(4, 10))

        # ① Token
        box1 = ttk.LabelFrame(outer, text=" ① GitHub 访问令牌（Token） ", padding=10)
        box1.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(box1)
        row.pack(fill="x")
        ttk.Label(row, text="Token", width=8).pack(side="left")
        self.entry_token = ttk.Entry(row, textvariable=self.var_token, show="•")
        self.entry_token.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="显示", width=6, command=self._toggle_show).pack(side="left")
        row2 = ttk.Frame(box1)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row2, text="记住 Token（Windows 加密保存）",
                        variable=self.var_remember).pack(side="left")
        ttk.Button(row2, text="验证登录", command=self.verify_token_async).pack(side="right")
        ttk.Button(row2, text="去获取 Token", command=self._open_token_page).pack(side="right", padx=6)
        ttk.Label(box1, foreground="#8a6d1f",
                  text="Token 不是密码。GitHub 已不支持用账号密码推送代码，必须用 Token（勾选 repo 权限）。"
                  ).pack(fill="x", pady=(6, 0))

        # ② 文件夹
        box2 = ttk.LabelFrame(outer, text=" ② 要上传的本地文件夹 ", padding=10)
        box2.pack(fill="x", pady=(0, 8))
        rowf = ttk.Frame(box2)
        rowf.pack(fill="x")
        ttk.Entry(rowf, textvariable=self.var_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(rowf, text="浏览…", command=self._pick_folder).pack(side="left", padx=(6, 0))

        # ③ 仓库
        box3 = ttk.LabelFrame(outer, text=" ③ GitHub 仓库 ", padding=10)
        box3.pack(fill="x", pady=(0, 8))
        rowm = ttk.Frame(box3)
        rowm.pack(fill="x")
        ttk.Radiobutton(rowm, text="创建新仓库", value="create", variable=self.var_mode,
                        command=self._on_mode).pack(side="left")
        ttk.Radiobutton(rowm, text="推送到已有仓库", value="existing", variable=self.var_mode,
                        command=self._on_mode).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(rowm, text="私有仓库", variable=self.var_private).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(rowm, text="推送前先合并远端", variable=self.var_auto_merge).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(rowm, text="强制覆盖远端", variable=self.var_force).pack(side="left", padx=(12, 0))

        rowr = ttk.Frame(box3)
        rowr.pack(fill="x", pady=(8, 0))
        ttk.Label(rowr, text="仓库名", width=8).pack(side="left")
        ttk.Entry(rowr, textvariable=self.var_repo).pack(side="left", fill="x", expand=True)

        rowd = ttk.Frame(box3)
        rowd.pack(fill="x", pady=(8, 0))
        ttk.Label(rowd, text="描述", width=8).pack(side="left")
        self.entry_desc = ttk.Entry(rowd, textvariable=self.var_desc)
        self.entry_desc.pack(side="left", fill="x", expand=True)

        rowc = ttk.Frame(box3)
        rowc.pack(fill="x", pady=(8, 0))
        ttk.Label(rowc, text="提交说明", width=8).pack(side="left")
        ttk.Entry(rowc, textvariable=self.var_message).pack(side="left", fill="x", expand=True)

        # 操作
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(2, 8))
        self.btn_go = ttk.Button(btns, text="开始上传", command=self.start_upload)
        self.btn_go.pack(side="left")
        self.btn_open = ttk.Button(btns, text="打开仓库网页", command=self._open_repo,
                                   state="disabled")
        self.btn_open.pack(side="left", padx=8)
        self.btn_pull = ttk.Button(btns, text="从 GitHub 拉取更新", command=self.start_pull)
        self.btn_pull.pack(side="left")
        ttk.Button(btns, text="清空日志", command=self._clear_log).pack(side="left", padx=8)

        # 日志
        box4 = ttk.LabelFrame(outer, text=" 运行日志 ", padding=6)
        box4.pack(fill="both", expand=True)
        self.log_box = scrolledtext.ScrolledText(
            box4, height=16, wrap="word", state="disabled",
            font=("Consolas", 9), background="#111418", foreground="#d8dee6",
            insertbackground="#d8dee6")
        self.log_box.pack(fill="both", expand=True)

        ttk.Label(outer, textvariable=self.var_status, anchor="w").pack(fill="x", pady=(8, 0))
        self._on_mode()

    # --------------------------------------------------------------- 小动作
    def _toggle_show(self) -> None:
        self.var_show.set(not self.var_show.get())
        self.entry_token.configure(show="" if self.var_show.get() else "•")

    def _open_token_page(self) -> None:
        webbrowser.open(TOKEN_URL)
        self.log("已在浏览器打开 Token 创建页（repo 权限已预勾选）。")
        self.log("步骤：Generate new token (classic) → 勾选 repo → Generate token → 复制那串 ghp_ 开头的字符。")

    def _on_mode(self) -> None:
        create = self.var_mode.get() == "create"
        self.entry_desc.configure(state="normal" if create else "disabled")

    def _on_close(self) -> None:
        if self.busy and not messagebox.askokcancel("退出", "正在上传，确定要退出吗？"):
            return
        self.save_settings()
        self.destroy()

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        for line in str(text).splitlines() or [""]:
            self.log_box.insert("end", f"[{stamp}] {line}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.var_status.set(text)

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(title="选择要上传到 GitHub 的文件夹",
                                         initialdir=self.var_folder.get() or str(Path.home()))
        if not chosen:
            return
        self.var_folder.set(chosen)
        if not self.var_repo.get().strip():
            self.var_repo.set(core.sanitize_repo_name(Path(chosen).name))

    def _open_repo(self) -> None:
        if self.last_url:
            webbrowser.open(self.last_url)

    # ----------------------------------------------------------- 后台任务
    def _busy_on(self, status: str) -> None:
        self.busy = True
        self.btn_go.configure(state="disabled")
        self.set_status(status)

    def _busy_off(self) -> None:
        self.busy = False
        self.btn_go.configure(state="normal")

    def verify_token_async(self) -> None:
        token = self.var_token.get().strip()
        if not token:
            messagebox.showwarning("缺少 Token", "请先填入 GitHub Token。\n点『去获取 Token』可以一键打开创建页。")
            return
        if self.busy:
            return
        self._busy_on("正在验证 Token…")
        self.log("正在验证 Token…")

        def work() -> None:
            try:
                user = core.whoami(token)
                self.q.put(("login", user))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def start_upload(self) -> None:
        if self.busy:
            return
        token = self.var_token.get().strip()
        folder = self.var_folder.get().strip()
        repo = self.var_repo.get().strip()
        if not token:
            messagebox.showwarning("缺少 Token", "请先填入 GitHub Token。")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("文件夹无效", "请选择一个存在的本地文件夹。")
            return
        repo = core.sanitize_repo_name(repo or Path(folder).name)
        self.var_repo.set(repo)
        try:
            core.validate_repo_name(repo)
        except core.GitHubError as exc:
            messagebox.showwarning("仓库名不合法", str(exc))
            return
        if self.var_force.get() and not messagebox.askokcancel(
                "确认强制覆盖", "强制推送会用本地历史覆盖远端同名分支，可能丢失 GitHub 上的提交。\n确定继续吗？"):
            self.var_force.set(False)
            return

        params = core.SyncParams(
            folder=Path(folder),
            repo=repo,
            token=token,
            login=self.login,
            mode=self.var_mode.get(),
            private=self.var_private.get(),
            description=self.var_desc.get().strip(),
            message=self.var_message.get().strip(),
            force=self.var_force.get(),
            auto_merge=self.var_auto_merge.get(),
        )
        self.save_settings()
        self._busy_on("正在上传…")
        self.log("─" * 52)
        self.log(f"开始上传：{folder}  →  {repo}")

        def work() -> None:
            try:
                result = core.sync_job(params, lambda m: self.q.put(("log", m)))
                self.q.put(("done", result))
            except core.GitHubError as exc:
                self.q.put(("error", str(exc)))
            except Exception:  # noqa: BLE001
                self.q.put(("error", "意外错误：\n" + traceback.format_exc(limit=4)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def start_pull(self) -> None:
        """只把 GitHub 上的更新合并到本地，不上传，也不覆盖任何提交。"""
        if self.busy:
            return
        token = self.var_token.get().strip()
        folder = self.var_folder.get().strip()
        if not token:
            messagebox.showwarning("缺少 Token", "请先填入 GitHub Token。")
            return
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("文件夹无效", "请选择一个本地文件夹。")
            return
        if not (Path(folder) / ".git").exists():
            messagebox.showwarning(
                "还不是 git 仓库",
                "这个文件夹本地还没有 git 仓库，没有可以合并的历史。\n请先用『开始上传』传一次。")
            return
        repo = core.sanitize_repo_name(self.var_repo.get().strip() or Path(folder).name)
        self.var_repo.set(repo)
        self.save_settings()
        self._busy_on("正在拉取远端更新…")
        self.log("─" * 52)
        self.log(f"从 GitHub 拉取更新：{repo} → {folder}")

        def work() -> None:
            try:
                user = core.whoami(token)
                login = self.login or str(user.get("login") or "")
                self.q.put(("log", f"已登录 GitHub：{login}"))
                state = core.merge_remote(Path(folder), login, repo, token, "main",
                                          lambda m: self.q.put(("log", m)))
                self.q.put(("pull_done", state))
            except core.GitHubError as exc:
                self.q.put(("error", str(exc)))
            except Exception:  # noqa: BLE001
                self.q.put(("error", "意外错误：\n" + traceback.format_exc(limit=4)))

        threading.Thread(target=work, daemon=True).start()

    def _on_pull_done(self, state: str) -> None:
        self._busy_off()
        labels = {
            "noop": "本地已是最新，没有需要合并的改动。",
            "ff": "已把远端更新快进合并到本地。",
            "rebased": "已把远端更新合并到本地（本地提交接在远端之后）。",
        }
        text = labels.get(state, "拉取完成。")
        self.set_status(text)
        self.log("✅ " + text)
        messagebox.showinfo("拉取完成", text)

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "login":
                    self._on_login(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "pull_done":
                    self._on_pull_done(payload)
                elif kind == "error":
                    self._busy_off()
                    self.set_status("失败")
                    self.log("❌ " + str(payload))
                    messagebox.showerror("操作失败", str(payload))
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _on_login(self, user: dict) -> None:
        self.login = str(user.get("login") or "")
        name = user.get("name") or self.login
        self.var_login.set(f"已登录：{self.login}")
        self._busy_off()
        self.set_status(f"Token 有效 · {self.login}")
        self.log(f"✅ Token 有效，登录账号：{self.login}" + (f"（{name}）" if name != self.login else ""))
        self.save_settings()

    def _on_done(self, result: dict) -> None:
        self._busy_off()
        self.last_url = result.get("url", "")
        self.var_login.set(f"已登录：{result.get('login', self.login)}")
        self.set_status("上传完成 · " + self.last_url)
        self.btn_open.configure(state="normal")
        self.log(f"仓库地址：{self.last_url}")
        self.log(f"共 {result.get('files', 0)} 个文件，{core.human_size(result.get('bytes', 0))}")
        self.save_settings()
        messagebox.showinfo("完成", f"代码已上传到 GitHub：\n{self.last_url}")

    # --------------------------------------------------------------- 配置
    def save_settings(self) -> None:
        cfg = store.load_config()
        cfg["last_folder"] = self.var_folder.get().strip()
        cfg["last_repo"] = self.var_repo.get().strip()
        cfg["private"] = bool(self.var_private.get())
        cfg["remember"] = bool(self.var_remember.get())
        cfg["mode"] = self.var_mode.get()
        cfg["auto_merge"] = bool(self.var_auto_merge.get())
        token = self.var_token.get().strip()
        if self.var_remember.get() and token:
            cfg["token"] = store.encrypt_secret(token)
        elif not self.var_remember.get():
            cfg.pop("token", None)
        try:
            store.save_config(cfg)
        except OSError as exc:
            self.log(f"（配置保存失败：{exc}）")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
