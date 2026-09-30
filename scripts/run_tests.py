"""轻量测试运行器：运行 tests/test_*.py 中所有 test_* 函数（无需 pytest）。

用法： python scripts/run_tests.py
"""

import importlib.util
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"

sys.path.insert(0, str(SRC))


def main() -> int:
    passed = failed = 0
    failures: list[str] = []
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("没有找到测试文件")
        return 1
    for f in files:
        spec = importlib.util.spec_from_file_location(f.stem, f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  ok   {f.stem}.{name}")
            except Exception:
                failed += 1
                failures.append(f"{f.stem}.{name}")
                print(f"  FAIL {f.stem}.{name}")
                traceback.print_exc()
    print(f"\n===== 结果：通过 {passed}，失败 {failed} =====")
    if failures:
        print("失败用例：")
        for x in failures:
            print(" -", x)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
