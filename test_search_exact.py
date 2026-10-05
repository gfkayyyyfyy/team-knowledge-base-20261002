"""search 子命令 --match exact 完整标题匹配的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 依次新增标题为 Straße备忘 / STRASSE / 道路Straße / Straße 的四篇文档后，
  以带首尾空白的 ``strasse`` 查询：省略 ``--match`` 与显式
  ``--match contains`` 的结果完全相同，默认按创建所得 ID 升序返回四篇；
- ``--match exact`` 只返回完整标题相等（Unicode casefold 后）的第二篇
  与第四篇，标题保留原文、版本均为 1；前缀（Straße备忘）与中间包含
  （道路Straße）均不算完整命中；查询词首尾空白先被去除；
- exact 配合 ``--sort id`` 或 ``--sort relevance`` 均按文档 ID 升序；
  配合 ``--offset 1 --limit 1`` 只返回第四篇，偏移量等于命中数时返回
  ``[]``；
- 成功查询退出码为 0、标准错误为空，标准输出是仅含 id、version、title
  的结果数组，不附加总数、页码等分页信息；
- 把第二篇更新为 部署指南（正文仍含 STRASSE）后，exact 查询只剩第四篇：
  旧标题与正文中的相同文字都不能使第二篇命中，历史仍保留原版本；
  查询前后索引、正文文件内容与历史记录逐一相同；
- 无命中或根目录不存在时 exact 查询返回 ``[]``、退出码 0，不创建目录
  或索引；
- ``--match`` 缺值或取未知值时退出码为 2、标准输出为空，标准错误说明
  参数问题且不含调用栈，即使根目录不存在也保持该错误结果。

每个用例使用独立临时目录与 UTF-8 正文，结束后清理自身数据，不依赖
已有文档或联网，可离线重复运行。
执行方式：``python -m unittest test_search_exact`` 或
``python -m unittest discover``。
"""

from __future__ import annotations

import json
import shutil
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


class ExactMatchTests(KbTestCase):
    """核心案例：Straße备忘 / STRASSE / 道路Straße / Straße 四篇文档。

    查询词为带首尾空白的 ``strasse``：``"Straße".casefold()`` 与
    ``"STRASSE".casefold()`` 均等于 ``"strasse"``，同时固定首尾空白
    去除与 Unicode casefold 比较。
    """

    TITLES = ["Straße备忘", "STRASSE", "道路Straße", "Straße"]
    QUERY = " \tstrasse\n "

    def setUp(self) -> None:
        super().setUp()
        # 预期结果一律用 add 返回的文档 ID 组织
        self.docs = [self.add_doc(t, f"{t} 的正文。\n") for t in self.TITLES]
        self.ids = [d["id"] for d in self.docs]

    def entry(self, index: int, version: int = 1) -> dict:
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_default_match_equals_explicit_contains(self):
        # 省略 --match 与显式 contains 的结果完全相同：
        # 默认按创建所得 ID 升序返回全部四篇
        expected = [self.entry(i) for i in range(4)]
        self.assert_search(self.QUERY, expected)
        self.assert_search(self.QUERY, expected, "--match", "contains")
        # 两种方式的标准输出逐字节一致
        default = run_cli("--root", str(self.kb), "search", self.QUERY)
        explicit = run_cli(
            "--root", str(self.kb), "search", self.QUERY,
            "--match", "contains",
        )
        self.assertEqual(default.returncode, 0, default.stderr.decode("utf-8"))
        self.assertEqual(explicit.returncode, 0,
                         explicit.stderr.decode("utf-8"))
        self.assertEqual(default.stdout, explicit.stdout)

    def test_exact_returns_only_full_title_matches(self):
        # exact 只返回第二篇 STRASSE 与第四篇 Straße，标题保留原文、
        # 版本均为 1；前缀（Straße备忘）与中间包含（道路Straße）不算命中
        self.assert_search(
            self.QUERY,
            [self.entry(1), self.entry(3)],
            "--match", "exact",
        )

    def test_exact_prefix_and_inner_contains_do_not_match(self):
        # 第一篇以查询词开头、第三篇在中间包含查询词，contains 命中而
        # exact 均不命中，证明完整相等才计入
        contains_hits = {self.ids[0], self.ids[1], self.ids[2], self.ids[3]}
        result = run_cli(
            "--root", str(self.kb), "search", self.QUERY,
            "--match", "contains",
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(
            {hit["id"] for hit in json.loads(result.stdout)}, contains_hits
        )
        exact = run_cli(
            "--root", str(self.kb), "search", self.QUERY,
            "--match", "exact",
        )
        self.assertEqual(exact.returncode, 0, exact.stderr.decode("utf-8"))
        self.assertEqual(
            {hit["id"] for hit in json.loads(exact.stdout)},
            {self.ids[1], self.ids[3]},
        )

    def test_exact_with_id_sort(self):
        self.assert_search(
            self.QUERY,
            [self.entry(1), self.entry(3)],
            "--match", "exact", "--sort", "id",
        )

    def test_exact_with_relevance_sort_is_id_order(self):
        # exact 下 relevance 不再分组，同样按文档 ID 升序
        self.assert_search(
            self.QUERY,
            [self.entry(1), self.entry(3)],
            "--match", "exact", "--sort", "relevance",
        )

    def test_exact_with_offset_and_limit(self):
        # 命中第二篇与第四篇，--offset 1 --limit 1 只返回第四篇
        self.assert_search(
            self.QUERY,
            [self.entry(3)],
            "--match", "exact", "--offset", "1", "--limit", "1",
        )

    def test_exact_offset_equal_to_hit_count_returns_empty(self):
        self.assert_search(
            self.QUERY, [], "--match", "exact", "--offset", "2"
        )

    def test_exact_output_has_no_pagination_metadata(self):
        # 输出仅为 JSON 数组本身，每项仅含 id、version、title，
        # 不附加总数、页码或其他分页信息
        result = run_cli(
            "--root", str(self.kb), "search", self.QUERY,
            "--match", "exact", "--offset", "1", "--limit", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout.decode("utf-8").count("{"), 1)
        self.assertNotIn("total", result.stdout.decode("utf-8"))
        hit = json.loads(result.stdout)[0]
        self.assertEqual(hit, self.entry(3))

    def test_exact_after_update_old_title_and_body_do_not_match(self):
        # 把第二篇更新为 部署指南，正文保留 STRASSE
        updated = self.update_doc(self.ids[1], "部署指南", "正文仍提到 STRASSE。\n")
        self.assertEqual(updated["version"], 2)

        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)

        # exact 查询只剩第四篇：旧标题 STRASSE 与正文中的相同文字
        # 都不能使第二篇命中
        self.assert_search(
            self.QUERY, [self.entry(3)], "--match", "exact"
        )
        # contains 下第二篇同样不再命中（只匹配最新标题，不搜正文）
        self.assert_search(
            self.QUERY,
            [self.entry(0), self.entry(2), self.entry(3)],
        )
        # 新标题的 exact 查询返回第二篇的最新版本
        self.assert_search(
            "部署指南",
            [{"id": self.ids[1], "version": 2, "title": "部署指南"}],
            "--match", "exact",
        )

        # 历史仍保留原版本：第二篇的版本 1（STRASSE）与版本 2（部署指南）
        # 均在修订记录中
        history = self.histories(self.ids)
        self.assertEqual(
            history[self.ids[1]],
            [
                {"version": 1, "title": "STRASSE"},
                {"version": 2, "title": "部署指南"},
            ],
        )
        # 查询前后索引、正文文件内容与历史记录逐一相同
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(history, before_history)

    def test_exact_search_does_not_modify_store(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)

        # 成功检索：省略 --match、显式 contains、exact 及各种排序/分页组合
        for extra in [
            (),
            ("--match", "contains"),
            ("--match", "exact"),
            ("--match", "exact", "--sort", "id"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--offset", "1", "--limit", "1"),
            ("--match", "exact", "--offset", "2"),
        ]:
            result = run_cli(
                "--root", str(self.kb), "search", self.QUERY, *extra
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")

        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)


class ExactEmptyResultTests(KbTestCase):
    """exact 空结果约定：输出 []、退出码 0、不产生任何目录或索引。"""

    def assert_empty_array(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")

    def test_exact_no_hits(self):
        self.add_doc("Straße备忘", "正文。\n")
        self.assert_empty_array(
            run_cli(
                "--root", str(self.kb), "search", "strasse",
                "--match", "exact",
            )
        )

    def test_exact_missing_root_returns_empty_and_creates_nothing(self):
        missing = self.tmp / "no_such_dir"
        self.assert_empty_array(
            run_cli(
                "--root", str(missing), "search", "strasse",
                "--match", "exact",
            )
        )
        self.assertFalse(missing.exists())

    def test_exact_dir_without_index_returns_empty_and_creates_nothing(self):
        self.kb.mkdir()
        self.assert_empty_array(
            run_cli(
                "--root", str(self.kb), "search", "strasse",
                "--match", "exact",
            )
        )
        self.assertEqual(list(self.kb.iterdir()), [])


class MatchArgumentErrorTests(KbTestCase):
    """--match 参数错误约定：退出码 2、标准输出为空、标准错误说明参数问题。"""

    def assert_match_error(self, args: tuple, reason: str) -> None:
        result = run_cli("--root", str(self.kb), "search", *args)
        self.assertEqual(result.returncode, 2,
                         f"退出码应为 2: {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertIn(reason, err)
        self.assertIn("--match", err)
        self.assertNotIn("Traceback", err)

    def test_match_missing_value(self):
        self.assert_match_error(("strasse", "--match"), "expected one argument")

    def test_match_unknown_value(self):
        self.assert_match_error(
            ("strasse", "--match", "prefix"), "invalid choice"
        )

    def test_match_invalid_even_when_root_missing(self):
        # 即使根目录不存在，--match 缺值或取未知值也保持参数错误结果：
        # 参数解析先于存储访问，退出码 2、标准输出为空，不创建目录
        missing = self.tmp / "no_such_dir"
        for args in [("strasse", "--match"), ("strasse", "--match", "prefix")]:
            with self.subTest(args=args):
                result = run_cli("--root", str(missing), "search", *args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                err = result.stderr.decode("utf-8")
                self.assertIn("--match", err)
                self.assertNotIn("Traceback", err)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
