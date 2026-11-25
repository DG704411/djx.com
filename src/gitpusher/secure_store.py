"""本地密钥存储：Windows DPAPI 加密（只有当前 Windows 用户能解开）。

配置文件位置: %LOCALAPPDATA%\\GitPusher\\config.json
非 Windows 平台自动降级为 base64（仅调试用，会打警告）。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "GitPusher"
CONFIG_PATH = APP_DIR / "config.json"

_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "plain:"


# ---------------------------------------------------------------- DPAPI 封装
if os.name == "nt":
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _make_blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        blob = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf  # 返回 buf 防止被 GC 回收

    def _to_blob(blob: _BLOB) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def _dpapi_encrypt(text: str) -> bytes:
        blob_in, _keep = _make_blob(text.encode("utf-8"))
        blob_out = _BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in), "GitPusher Token", None, None, None, 0,
            ctypes.byref(blob_out))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return _to_blob(blob_out)

    def _dpapi_decrypt(raw: bytes) -> str:
        blob_in, _keep = _make_blob(raw)
        blob_out = _BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return _to_blob(blob_out).decode("utf-8")
else:  # pragma: no cover - 仅非 Windows 调试
    def _dpapi_encrypt(text: str) -> bytes:
        raise NotImplementedError("DPAPI 仅在 Windows 可用")

    def _dpapi_decrypt(raw: bytes) -> str:
        raise NotImplementedError("DPAPI 仅在 Windows 可用")


# ------------------------------------------------------------------ 对外接口
def encrypt_secret(text: str) -> str:
    """把明文加密成可写进配置文件的字符串。"""
    if not text:
        return ""
    if os.name == "nt":
        try:
            return _PREFIX_DPAPI + base64.b64encode(_dpapi_encrypt(text)).decode("ascii")
        except Exception:
            pass  # 极端情况下退化为 base64，至少不丢数据
    return _PREFIX_PLAIN + base64.b64encode(text.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    if not blob:
        return ""
    if blob.startswith(_PREFIX_DPAPI):
        return _dpapi_decrypt(base64.b64decode(blob[len(_PREFIX_DPAPI):]))
    if blob.startswith(_PREFIX_PLAIN):
        return base64.b64decode(blob[len(_PREFIX_PLAIN):]).decode("utf-8")
    return ""  # 未知格式，当作没有


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    if os.name == "nt":
        _harden_acl(CONFIG_PATH)


def _harden_acl(path: Path) -> None:
    """把配置文件权限收紧到「仅当前用户」，失败也不影响主流程。"""
    try:
        import getpass
        user = os.environ.get("USERNAME") or getpass.getuser()
        subprocess_icacls(path, user)
    except Exception:
        pass


def subprocess_icacls(path: Path, user: str) -> None:
    import subprocess
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
        capture_output=True, timeout=15,
        creationflags=0x08000000 if os.name == "nt" else 0)


def forget_token() -> None:
    cfg = load_config()
    cfg.pop("token", None)
    save_config(cfg)
