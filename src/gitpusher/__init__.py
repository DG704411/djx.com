"""代码上传助手 · GitHub —— 把本地代码一键提交并推送到 GitHub。

纯 Python 标准库实现，**没有任何第三方运行时依赖**。

模块说明::

    gitpusher.core           GitHub API + git 流水线（无界面，可单测）
    gitpusher.secure_store   Token 的 Windows DPAPI 加密存储
    gitpusher.gui            tkinter 桌面界面
    gitpusher.cli            命令行入口
"""

__version__ = "1.0.2"
__all__ = ["__version__"]
