"""search 子命令 --match exact 完整标题匹配的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- ``--match`` 仅接受 ``contains`` 与 ``exact``，省略时等同于 ``contains``，
  保持既有的字面子串检索行为；
- ``--match exact`` 时只在最新标题与查询词经 Unicode casefold 后完整相等
  时命中，以查询词开头（前缀）与标题中间包含均不算命中；查询词仍去除
  首尾空白、内部空格原样保留，``%``、``_``、``*``、``[``、``]`` 仍为
  普通字符，Straße 与 STRASSE 视为相等；
- exact 下只匹配最新标题，不搜正文与历史标题，同名不同文档各返回一条；
- exact 下 ``--sort id`` 与 ``--sort relevance`` 均按文档 ID 升序；
- exact 下先筛选再排序，``--offset``/``--limit`` 统计的是筛选后的命中数，
  偏移越界输出 ``[]``，无命中、根目录不存在、尚无索引同样输出 ``[]``
  （退出码 0、标准错误为空）；
- ``--match`` 缺值、空串或其他取值，即使根目录不存在也以退出码 2 报错，
  标准输出为空，标准错误说明原因；根路径不是目录、已有索引无法查询同样
  报错，不输出空数组伪装成功；
- 成功与失败检索均不改变知识库目录、索引与正文，不创建修订。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_search_exact`` 或
``python -m unittest discover``。
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
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_search_exact_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, body: str) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_text(body, encoding="utf-8")
        return body_file

    def add_doc(self, title: str, body: str = "正文。\n") -> dict:
        """通过 add 命令新增文档，返回解析后的 {"id", "version", "title"}。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def update_doc(self, doc_id: int, title: str, body: str = "正文。\n") -> dict:
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
        """断言检索成功：退出码 0、标准错误为空、结果与预期完全一致。"""
        result = run_cli("--root", str(self.kb), "search", query, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        hits = json.loads(result.stdout)
        self.assertEqual(hits, expected)
        for hit in hits:
            self.assertEqual(set(hit), {"id", "version", "title"})


class ExactMatchAcceptanceTests(KbTestCase):
    """验收四篇：部署手册 / 部署(v2) / 预部署检查 / 部署。"""

    TITLES = ["部署手册", "部署", "预部署检查", "部署"]

    def setUp(self) -> None:
        super().setUp()
        self.docs = [self.add_doc(t) for t in self.TITLES]
        self.ids = [d["id"] for d in self.docs]
        # ID 2 更新到版本 2，标题不变
        updated = self.update_doc(self.ids[1], "部署")
        self.assertEqual(updated["version"], 2)

    def entry(self, index: int, version: int | None = None) -> dict:
        if version is None:
            version = 2 if index == 1 else 1
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_acceptance_exact_relevance_offset_one_limit_one(self):
        # 验收场景：exact 只命中 ID 2、4，两种排序都按 ID 升序，
        # 跳过第 1 条（ID 2）后只返回 ID 4
        self.assert_search(
            "部署",
            [self.entry(3)],
            "--match", "exact", "--sort", "relevance",
            "--offset", "1", "--limit", "1",
        )

    def test_exact_returns_only_equal_titles_in_id_order(self):
        # 部署手册（前缀）与预部署检查（中间包含）都不算命中
        self.assert_search(
            "部署",
            [self.entry(1), self.entry(3)],
            "--match", "exact",
        )

    def test_exact_both_sorts_are_id_order(self):
        expected = [self.entry(1), self.entry(3)]
        self.assert_search(
            "部署", expected, "--match", "exact", "--sort", "id"
        )
        self.assert_search(
            "部署", expected, "--match", "exact", "--sort", "relevance"
        )

    def test_omitting_match_keeps_contains_behavior(self):
        # 省略 --match、--sort 与分页参数时，同一查询按 ID 返回全部四篇
        self.assert_search(
            "部署",
            [self.entry(0), self.entry(1), self.entry(2), self.entry(3)],
        )
        # 显式 contains 与省略完全一致
        self.assert_search(
            "部署",
            [self.entry(0), self.entry(1), self.entry(2), self.entry(3)],
            "--match", "contains",
        )

    def test_exact_pagination_counts_filtered_hits(self):
        # 筛选后只有 2 条命中：偏移 2 已越界，输出 []
        self.assert_search("部署", [], "--match", "exact", "--offset", "2")
        self.assert_search(
            "部署", [],
            "--match", "exact", "--sort", "relevance",
            "--offset", "9", "--limit", "1",
        )
        # 偏移 1 后不加限制，只剩 ID 4
        self.assert_search(
            "部署", [self.entry(3)], "--match", "exact", "--offset", "1"
        )
        # 剩余 1 条但上限更大时不补空项
        self.assert_search(
            "部署", [self.entry(3)],
            "--match", "exact", "--offset", "1", "--limit", "9",
        )

    def test_exact_limit_one_returns_first_equal_doc(self):
        self.assert_search(
            "部署", [self.entry(1)], "--match", "exact", "--limit", "1"
        )
        self.assert_search(
            "部署", [self.entry(1)],
            "--match", "exact", "--sort", "relevance", "--limit", "1",
        )

    def test_exact_prefix_and_inner_contain_do_not_hit(self):
        # 完整标题更长或查询词只是其中一部分时均不命中
        self.assert_search("部", [], "--match", "exact")
        self.assert_search("署", [], "--match", "exact")
        self.assert_search("部署手", [], "--match", "exact")
        self.assert_search("预部署", [], "--match", "exact")
        self.assert_search("部署x", [], "--match", "exact")
        self.assert_search("x部署", [], "--match", "exact")

    def test_exact_query_whitespace_handling(self):
        # 查询词首尾空白被去除后仍与标题完整相等
        self.assert_search(
            "  部署\t",
            [self.entry(1), self.entry(3)],
            "--match", "exact",
        )
        # 内部空格原样保留：标题中没有该空格，不命中
        self.assert_search("部 署", [], "--match", "exact")

    def test_exact_latest_title_only(self):
        # ID 2 的旧版本标题与最新相同但正文不同，exact 只返回最新版本一条；
        # 把 ID 1 更新为其他标题后，exact 不再通过旧标题命中它
        self.update_doc(self.ids[0], "归档", "新正文仍提到部署。\n")
        self.assert_search(
            "部署",
            [self.entry(1), self.entry(3)],
            "--match", "exact",
        )
        self.assert_search(
            "部署手册", [], "--match", "exact",
        )
        self.assert_search(
            "归档",
            [{"id": self.ids[0], "version": 2, "title": "归档"}],
            "--match", "exact",
        )

    def test_exact_casefold_equality(self):
        # Straße 与 STRASSE 经 casefold 后完整相等
        doc = self.add_doc("Straße")
        self.assert_search(
            "STRASSE",
            [{"id": doc["id"], "version": 1, "title": "Straße"}],
            "--match", "exact",
        )
        # 前缀/包含形态在 exact 下不命中
        self.add_doc("STRASSE 指南")
        self.assert_search(
            "STRASSE",
            [{"id": doc["id"], "version": 1, "title": "Straße"}],
            "--match", "exact",
        )
        # 小写查询经 casefold 后同样完整相等
        self.assert_search(
            "strasse",
            [{"id": doc["id"], "version": 1, "title": "Straße"}],
            "--match", "exact",
        )
        # 大小写不同但更长的标题不算完整相等
        self.assert_search("straße x", [], "--match", "exact")
        self.assert_search("strass", [], "--match", "exact")

    def test_exact_literal_symbols(self):
        # %、_、*、[、] 仍是普通字符，exact 只认完整相等
        special = self.add_doc("100%")
        self.add_doc("100%指南")
        self.add_doc("100X")
        self.assert_search(
            "100%",
            [{"id": special["id"], "version": 1, "title": "100%"}],
            "--match", "exact",
        )
        self.assert_search("100", [], "--match", "exact")

    def test_search_does_not_modify_store(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)
        for extra in [
            ("--match", "exact"),
            ("--match", "exact", "--sort", "id"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--offset", "1", "--limit", "1"),
            ("--match", "contains"),
            (),
        ]:
            result = run_cli("--root", str(self.kb), "search", "部署", *extra)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)


class ExactEmptyResultTests(KbTestCase):
    """exact 下空结果约定：[]、退出码 0、标准错误为空、不创建任何东西。"""

    def assert_empty_array(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")

    def test_no_hits(self):
        self.add_doc("部署手册")
        self.assert_empty_array(
            run_cli("--root", str(self.kb), "search", "部署", "--match", "exact")
        )

    def test_missing_root_returns_empty_and_creates_nothing(self):
        missing = self.tmp / "no_such_dir"
        self.assert_empty_array(
            run_cli(
                "--root", str(missing), "search", "部署",
                "--match", "exact", "--sort", "relevance",
                "--offset", "3", "--limit", "2",
            )
        )
        self.assertFalse(missing.exists())

    def test_dir_without_index_returns_empty_and_creates_nothing(self):
        self.kb.mkdir()
        self.assert_empty_array(
            run_cli("--root", str(self.kb), "search", "部署", "--match", "exact")
        )
        self.assertEqual(list(self.kb.iterdir()), [])


class ExactStorageUnavailableTests(KbTestCase):
    """exact 下根路径不是目录、索引无法查询仍以退出码 2 报错。"""

    def assert_fails(self, reason: str) -> None:
        for extra in [
            ("--match", "exact"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--offset", "999999", "--limit", "2"),
        ]:
            result = run_cli(
                "--root", str(self.kb), "search", "部署", *extra
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            err = result.stderr.decode("utf-8")
            self.assertIn(reason, err)
            self.assertNotIn("Traceback", err)

    def test_root_is_regular_file(self):
        self.kb.write_text("占用根路径的普通文件。\n", encoding="utf-8")
        self.assert_fails("不是目录")

    def test_index_is_plain_text(self):
        self.kb.mkdir()
        (self.kb / "knowledge_base.sqlite3").write_text(
            "这不是 SQLite 数据库，只是普通文本。\n", encoding="utf-8"
        )
        self.assert_fails("索引无法打开或查询失败")

    def test_index_missing_versions_table(self):
        self.kb.mkdir()
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        with conn:
            conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
        conn.close()
        self.assert_fails("索引无法打开或查询失败")


class ExactMatchArgumentErrorTests(KbTestCase):
    """--match 参数错误约定：退出码 2、标准输出为空、标准错误说明原因。"""

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

    def test_match_missing_value(self):
        self.assert_error(("部署", "--match"), "expected one argument")

    def test_match_empty_value(self):
        self.assert_error(("部署", "--match", ""), "invalid choice")

    def test_match_invalid_values(self):
        for bad in ["EXACT", "Exact", "equal", "prefix", "like", "exactly"]:
            with self.subTest(bad=bad):
                self.assert_error(("部署", "--match", bad), "invalid choice")

    def test_match_invalid_even_when_root_missing(self):
        # 即使根目录不存在，非法 --match 也不能被当成成功的空结果
        missing = self.tmp / "no_such_dir"
        for bad in ["", "exact!", "same", "EXACT"]:
            with self.subTest(bad=bad):
                result = run_cli(
                    "--root", str(missing), "search", "部署", "--match", bad
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn("--match", result.stderr.decode("utf-8"))
        self.assertFalse(missing.exists())

    def test_existing_argument_errors_still_apply_with_exact(self):
        for args, reason in [
            (("   ", "--match", "exact"), "QUERY"),
            (("部署", "--match", "exact", "--sort", "name"), "invalid choice"),
            (("部署", "--match", "exact", "--limit", "0"), "数量参数"),
            (("部署", "--match", "exact", "--limit", "1.5"), "数量参数"),
            (("部署", "--match", "exact", "--offset", "-1"), "偏移量参数"),
            (("部署", "--match", "exact", "--offset", ""), "偏移量参数"),
        ]:
            with self.subTest(args=args):
                self.assert_error(args, reason)

    def test_argument_error_creates_nothing(self):
        self.assert_error(("部署", "--match", ""), "invalid choice")
        self.assertFalse(self.kb.exists())


if __name__ == "__main__":
    unittest.main()
