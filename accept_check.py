"""show 正文异常约定的验收测试直接运行入口。

用例本体在 test_show.py 中，由 ``python -m unittest discover`` 统一收集；
本脚本保留直接运行入口，全部通过时打印“全部验收场景通过”，
任何断言失败都以非零退出码结束。
"""
import sys
import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromName("test_show")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("全部验收场景通过")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
