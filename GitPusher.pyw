#!/usr/bin/env python
"""双击启动器：把 src 加入模块搜索路径，然后打开界面。

保留这个文件是为了让「双击 .pyw 直接运行」这种最省事的方式一直可用，
同时让代码本身保持标准的 src 布局（可 pip 安装、可被 pytest 导入）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from gitpusher.gui import main  # noqa: E402  (必须在插入 sys.path 之后导入)

main()
