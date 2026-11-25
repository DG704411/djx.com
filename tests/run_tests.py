"""零依赖测试运行器：没装 pytest 也能跑完整套测试。

用法：

    python tests/run_tests.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import test_core  # noqa: E402


def collect() -> list[str]:
    names = [
        name for name in dir(test_core)
        if name.startswith("test_") and callable(getattr(test_core, name))
    ]
    names.sort(key=lambda name: getattr(test_core, name).__code__.co_firstlineno)
    return names


def main() -> int:
    names = collect()
    passed: list[str] = []
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, BaseException]] = []

    print("=" * 78)
    for name in names:
        fn = getattr(test_core, name)
        started = time.time()
        try:
            fn()
        except test_core.SkipTest as exc:
            skipped.append((name, str(exc)))
            print(f"[SKIP] {name:<38} {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            elapsed = time.time() - started
            print(f"[FAIL] {name:<38} {elapsed:5.2f}s  {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=2)
        else:
            elapsed = time.time() - started
            passed.append(name)
            print(f"[PASS] {name:<38} {elapsed:5.2f}s")

    cleaned, left = test_core.cleanup_temp_dirs()
    print("=" * 78)
    print(f"通过 {len(passed)} · 跳过 {len(skipped)} · 失败 {len(failed)} · 共 {len(names)}")
    print(f"已清理临时目录 {cleaned} 个")
    if left:
        print("未能删除：" + ", ".join(left))
    if failed:
        print("\n失败用例：")
        for name, exc in failed:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
