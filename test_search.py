"""search 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 默认排序与显式 ``--sort id`` 按文档 ID 升序；``--sort relevance`` 按
  完全相等 / 前缀 / 其余包含分组，同组按 ID 升序；
- ``--limit N``（正整数）只返回最终排序结果的前 N 条，与不设上限的
  同一查询前 N 条完全一致；缺值、空值、零、负数、小数或非整数以
  退出码 2 报错，标准输出为空，即使根目录不存在也不当成空结果；
- 每条结果只含 id、version、title，标题保留原文；
- 匹配与分组使用 Unicode casefold 语义；
- QUERY 去除首尾空白，内部空格按字面保留；
- 只匹配最新标题，不搜正文与历史标题；
- QUERY 缺失/空白、--sort 缺值/非法取值以退出码 2 报错，标准输出为空；
- 无命中、根目录不存在、目录尚无索引时输出 ``[]``（退出码 0），
  不创建目录或索引；
- 根路径不是目录、索引文件不是数据库、索引缺少 versions 表时，
  以退出码 2 报错，标准输出为空，不输出空数组伪装成功，
  失败检索不修复或改写输入；
- 成功与失败检索均不改变知识库文件内容与修订记录。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_search`` 或 ``python -m unittest discover``。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # knowledge_base 包所在目录


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """以公开入口运行命令，捕获退出码与标准输出/错误，不检查退出码。"""
    return subprocess.run(
        [sys.executable, "-m", "knowledge_base", *args],
        capture_output=True,
        cwd=ROOT,
    )


def snapshot(kb_dir: Path) -> list:
    """递归快照知识库目录内全部条目与文件内容，用于只读性断言。"""
    return sorted(
        (str(p.relative_to(kb_dir)), p.read_bytes() if p.is_file() else b"<dir>")
        for p in kb_dir.rglob("*")
    )


class KbTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_search_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, body: str) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_text(body, encoding="utf-8")
        return body_file

    def add_doc(self, title: str, body: str) -> dict:
        """通过 add 命令新增文档，返回解析后的 {"id", "version", "title"}。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def update_doc(self, doc_id: int, title: str, body: str) -> dict:
        """通过 update 命令更新文档，返回解析后的 {"id", "version", "title"}。"""
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def histories(self, doc_ids) -> dict:
        """通过 history 命令读取各文档修订记录。"""
        records = {}
        for doc_id in doc_ids:
            result = run_cli("--root", str(self.kb), "history", str(doc_id))
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            records[doc_id] = json.loads(result.stdout)
        return records

    def assert_search(self, query: str, expected: list, *extra: str) -> None:
        """断言检索成功：退出码 0、标准错误为空、结果与预期完全一致。

        expected 为 [{"id", "version", "title"}, ...]，顺序与版本号均核对；
        同时固定每条结果只含 id、version、title 三个字段。
        """
        result = run_cli("--root", str(self.kb), "search", query, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        hits = json.loads(result.stdout)
        self.assertEqual(hits, expected)
        for hit in hits:
            self.assertEqual(set(hit), {"id", "version", "title"})


class RelevanceSortTests(KbTestCase):
    """依次新增 发布流程 / 预发布检查 / 发布 / 发布记录 四篇文档后的排序行为。"""

    TITLES = ["发布流程", "预发布检查", "发布", "发布记录"]

    def setUp(self) -> None:
        super().setUp()
        # 预期结果一律用 add 返回的文档 ID 组织
        self.docs = [self.add_doc(t, f"{t} 的正文。\n") for t in self.TITLES]
        self.ids = [d["id"] for d in self.docs]

    def entry(self, index: int, version: int = 1) -> dict:
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_default_sort_is_id_order(self):
        self.assert_search(
            "发布",
            [self.entry(0), self.entry(1), self.entry(2), self.entry(3)],
        )

    def test_explicit_id_sort(self):
        self.assert_search(
            "发布",
            [self.entry(0), self.entry(1), self.entry(2), self.entry(3)],
            "--sort", "id",
        )

    def test_relevance_sort_groups(self):
        # 完全相等（发布）最前，前缀（发布流程、发布记录）其次，
        # 其余包含（预发布检查）最后，同组按 ID 升序
        self.assert_search(
            "发布",
            [self.entry(2), self.entry(0), self.entry(3), self.entry(1)],
            "--sort", "relevance",
        )

    def test_surrounding_whitespace_stripped(self):
        # 首尾空白（含制表符、换行）被去除，结果与裸查询一致
        self.assert_search(
            " \t发布\n ",
            [self.entry(2), self.entry(0), self.entry(3), self.entry(1)],
            "--sort", "relevance",
        )

    def test_update_removes_old_title_from_search(self):
        # 第三篇更新为 归档说明，正文仍含“发布”
        updated = self.update_doc(self.ids[2], "归档说明", "归档说明，仍提到发布。\n")
        self.assertEqual(updated["version"], 2)

        # relevance：只剩 发布流程、发布记录（前缀）与 预发布检查（包含）
        self.assert_search(
            "发布",
            [self.entry(0), self.entry(3), self.entry(1)],
            "--sort", "relevance",
        )
        # id 排序同样不再命中第三篇（旧标题与正文均不产生命中）
        self.assert_search(
            "发布",
            [self.entry(0), self.entry(1), self.entry(3)],
        )
        # 新标题查询返回该文档的最新版本
        self.assert_search(
            "归档说明",
            [{"id": self.ids[2], "version": 2, "title": "归档说明"}],
        )

    def test_search_does_not_modify_store(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)

        # 成功检索：默认、显式 id、relevance
        for extra in [(), ("--sort", "id"), ("--sort", "relevance")]:
            result = run_cli("--root", str(self.kb), "search", "发布", *extra)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        # 失败检索：QUERY 空白、--sort 非法取值
        for args in [("   ",), ("发布", "--sort", "name")]:
            result = run_cli("--root", str(self.kb), "search", *args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")

        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)


class UnicodeCasefoldTests(KbTestCase):
    """Unicode casefold 语义下的匹配与相等分组。"""

    def setUp(self) -> None:
        super().setUp()
        # 先新增前缀文档，再新增完全相等文档，使分组顺序与 ID 顺序相反
        self.prefix_doc = self.add_doc("STRASSE 指南", "街道指南正文。\n")
        self.exact_doc = self.add_doc("Straße", "街道正文。\n")

    def test_casefold_equal_group(self):
        # "Straße".casefold() == "strasse" == "STRASSE".casefold()，
        # 完全相等组先于前缀组，标题保留原文
        self.assert_search(
            "STRASSE",
            [
                {"id": self.exact_doc["id"], "version": 1, "title": "Straße"},
                {"id": self.prefix_doc["id"], "version": 1,
                 "title": "STRASSE 指南"},
            ],
            "--sort", "relevance",
        )

    def test_casefold_id_sort(self):
        # 小写查询同样命中两者，id 排序按 ID 升序
        self.assert_search(
            "strasse",
            [
                {"id": self.prefix_doc["id"], "version": 1,
                 "title": "STRASSE 指南"},
                {"id": self.exact_doc["id"], "version": 1, "title": "Straße"},
            ],
        )


class QueryWhitespaceTests(KbTestCase):
    """查询词内部空格按字面保留。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc = self.add_doc("发布 流程", "带内部空格的标题正文。\n")

    def test_internal_space_matches_literally(self):
        self.assert_search(
            "发布 流程",
            [{"id": self.doc["id"], "version": 1, "title": "发布 流程"}],
        )

    def test_internal_space_cannot_be_omitted(self):
        # 去掉内部空格后不再命中，证明空格按字面参与匹配
        self.assert_search("发布流程", [])


class EmptyResultTests(KbTestCase):
    """空结果约定：输出 []、退出码 0、不产生任何目录或索引。"""

    def assert_empty_array(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")

    def test_no_hits(self):
        self.add_doc("发布流程", "正文。\n")
        self.assert_empty_array(
            run_cli("--root", str(self.kb), "search", "不存在的关键词")
        )

    def test_missing_root_returns_empty_and_creates_nothing(self):
        missing = self.tmp / "no_such_dir"
        self.assert_empty_array(run_cli("--root", str(missing), "search", "发布"))
        self.assertFalse(missing.exists())

    def test_dir_without_index_returns_empty_and_creates_nothing(self):
        self.kb.mkdir()
        self.assert_empty_array(run_cli("--root", str(self.kb), "search", "发布"))
        self.assertEqual(list(self.kb.iterdir()), [])


class StorageUnavailableTests(KbTestCase):
    """本地存储不可用时的检索约定。

    与 EmptyResultTests 对照：根目录不存在或目录尚无索引属于“尚未初始化”，
    返回 []（退出码 0）；而根路径不是目录、已有索引无法打开或查询失败
    属于“已有存储不可用”，必须以退出码 2 报错，标准输出为空，
    不输出空数组伪装成功，不泄露 Traceback，也不修复或改写输入。
    """

    QUERY = "发布"

    def assert_search_fails(self, reason: str) -> None:
        """默认排序与 --sort relevance 均以退出码 2 失败，且输入保持原样。"""
        before = snapshot(self.tmp)
        for extra in [(), ("--sort", "relevance")]:
            result = run_cli(
                "--root", str(self.kb), "search", self.QUERY, *extra
            )
            label = extra[-1] if extra else "默认排序"
            self.assertEqual(
                result.returncode, 2,
                f"{label}: 退出码应为 2: {result.returncode}",
            )
            self.assertEqual(
                result.stdout, b"",
                f"{label}: 标准输出应为空（不得输出空数组伪装成功）: "
                f"{result.stdout!r}",
            )
            err = result.stderr.decode("utf-8")
            self.assertIn(reason, err, f"{label}: 标准错误应说明原因")
            self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈")
        # 失败检索不修复或改写输入：目录条目与文件字节逐一对比，
        # 不新增正文目录、索引表或其他文件
        self.assertEqual(snapshot(self.tmp), before)

    def test_root_is_regular_file(self):
        # --root 指向普通文件：报告根路径不是目录
        self.kb.write_text("占用根路径的普通文件。\n", encoding="utf-8")
        self.assert_search_fails("不是目录")

    def test_index_is_plain_text(self):
        # 根目录存在，knowledge_base.sqlite3 是普通 UTF-8 文本
        self.kb.mkdir()
        (self.kb / "knowledge_base.sqlite3").write_text(
            "这不是 SQLite 数据库，只是普通文本。\n", encoding="utf-8"
        )
        self.assert_search_fails("索引无法打开或查询失败")

    def test_index_missing_versions_table(self):
        # 索引是可打开的 SQLite 数据库，但没有 versions 表
        self.kb.mkdir()
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        with conn:
            conn.execute(
                "CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)"
            )
        conn.close()
        self.assert_search_fails("索引无法打开或查询失败")


class LimitTests(KbTestCase):
    """--limit N：只返回最终排序结果的前 N 条。

    N 为正整数；省略时返回全部命中。带上限的结果与同一查询不设上限的
    前 N 条完全一致，命中不足 N 条时返回全部命中，不补空项。
    """

    TITLES = ["发布流程", "预发布检查", "发布", "发布记录"]

    def setUp(self) -> None:
        super().setUp()
        self.docs = [self.add_doc(t, f"{t} 的正文。\n") for t in self.TITLES]
        self.ids = [d["id"] for d in self.docs]

    def entry(self, index: int, version: int = 1) -> dict:
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_limit_default_id_sort(self):
        self.assert_search(
            "发布", [self.entry(0), self.entry(1)], "--limit", "2"
        )

    def test_limit_relevance_sort(self):
        # 上限作用于 relevance 分组排序之后：完全相等（发布）、前缀（发布流程）
        self.assert_search(
            "发布", [self.entry(2), self.entry(0)],
            "--sort", "relevance", "--limit", "2",
        )

    def test_limit_matches_prefix_of_unlimited(self):
        for extra in [(), ("--sort", "relevance")]:
            unlimited = json.loads(run_cli(
                "--root", str(self.kb), "search", "发布", *extra
            ).stdout)
            for n in (1, 2, 3, 4):
                limited = json.loads(run_cli(
                    "--root", str(self.kb), "search", "发布",
                    *extra, "--limit", str(n),
                ).stdout)
                self.assertEqual(limited, unlimited[:n])

    def test_limit_larger_than_hits_returns_all(self):
        self.assert_search(
            "发布",
            [self.entry(0), self.entry(1), self.entry(2), self.entry(3)],
            "--limit", "10",
        )

    def test_limit_no_hits(self):
        result = run_cli(
            "--root", str(self.kb), "search", "不存在的关键词", "--limit", "2"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")

    def test_limit_missing_root_creates_nothing(self):
        missing = self.tmp / "no_such_dir"
        result = run_cli(
            "--root", str(missing), "search", "发布", "--limit", "2"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")
        self.assertFalse(missing.exists())

    def test_limit_dir_without_index_creates_nothing(self):
        empty = self.tmp / "empty_kb"
        empty.mkdir()
        result = run_cli(
            "--root", str(empty), "search", "发布", "--limit", "2"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"[]\n")
        self.assertEqual(list(empty.iterdir()), [])

    def test_limit_does_not_modify_store(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)
        for extra in [("--limit", "1"), ("--sort", "relevance", "--limit", "2")]:
            result = run_cli("--root", str(self.kb), "search", "发布", *extra)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)


class LimitArgumentErrorTests(KbTestCase):
    """--limit 非法取值：退出码 2、标准输出为空，即使根目录不存在也报错。"""

    def assert_limit_error(self, *args: str) -> None:
        # 根目录刻意不存在：非法数量仍须报错，不能当成成功的空结果
        missing = self.tmp / "no_such_dir"
        result = run_cli("--root", str(missing), "search", "发布", *args)
        self.assertEqual(result.returncode, 2,
                         f"退出码应为 2: {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertIn("--limit", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(missing.exists())

    def test_missing_value(self):
        self.assert_limit_error("--limit")

    def test_empty_value(self):
        self.assert_limit_error("--limit", "")

    def test_zero(self):
        self.assert_limit_error("--limit", "0")

    def test_negative(self):
        self.assert_limit_error("--limit", "-3")

    def test_decimal(self):
        self.assert_limit_error("--limit", "2.5")

    def test_non_integer(self):
        self.assert_limit_error("--limit", "abc")


class SearchArgumentErrorTests(KbTestCase):
    """参数错误约定：退出码 2、标准输出为空、标准错误给出原因。"""

    def assert_error(self, args: tuple, reason: str) -> subprocess.CompletedProcess:
        result = run_cli("--root", str(self.kb), "search", *args)
        self.assertEqual(result.returncode, 2,
                         f"退出码应为 2: {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertIn(reason, err)
        self.assertNotIn("Traceback", err)
        return result
    def test_missing_query(self):
        self.assert_error((), "QUERY")

    def test_blank_query(self):
        self.assert_error(("   ",), "QUERY 去除首尾空白后不能为空")

    def test_sort_missing_value(self):
        self.assert_error(("发布", "--sort"), "expected one argument")

    def test_sort_invalid_value(self):
        self.assert_error(("发布", "--sort", "name"), "invalid choice")

    def test_argument_error_creates_nothing(self):
        # 参数错误在访问存储之前失败，不创建知识库目录
        self.assert_error(("   ",), "QUERY")
        self.assertFalse(self.kb.exists())


if __name__ == "__main__":
    unittest.main()
