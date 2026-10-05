"""search 子命令 ``--match exact``（按最新标题完整匹配）的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
把既有的完整标题匹配行为固定下来：

- 核心案例依次新增标题为 Straße备忘、STRASSE、道路Straße、Straße 的四篇
  文档（版本均为 1），再以带首尾空白的小写 ``strasse`` 查询；
- 省略 ``--match`` 与显式 ``--match contains`` 结果相同，默认按创建所得
  ID 升序返回四篇；
- ``--match exact`` 只返回第二篇（STRASSE）与第四篇（Straße），标题保留
  原文、版本均为 1；这同时固定首尾空白处理与 Unicode casefold 比较
  （小写 strasse 经 casefold 等于 STRASSE 与 Straße），前缀命中
  （Straße备忘）与中间/尾部包含（道路Straße）都不算完整命中；
- exact 配合 id 或 relevance 排序都按文档 ID 升序；
  ``--offset 1 --limit 1`` 只返回第四篇，偏移等于命中数时返回 ``[]``；
- 所有成功查询退出码为 0、标准错误为空，标准输出是仅含 id、version、
  title 的结果数组，不增加分页信息；
- 第二篇更新为“部署指南”（正文仍保留 STRASSE 字样）后，exact 查询只剩
  第四篇，旧标题与正文中的相同文字都不能使第二篇命中，历史仍保留版本 1；
  查询前后索引、正文文件内容与历史记录逐一相同；
- 无命中或根目录不存在时，exact 查询输出 ``[]``、退出码 0，不创建目录或
  索引；``--match`` 缺值或取未知值时退出码 2、标准输出为空，标准错误说明
  参数问题且不含调用栈，即使根目录不存在也保持该错误结果。

每个用例使用独立临时目录与 UTF-8 正文，结束后清理自身数据，不依赖联网或
已有数据，可离线重复运行。
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

# 带首尾空白（空格、制表符、换行）的小写查询词：同时固定 QUERY 去空白与
# Unicode casefold（ß casefold 为 ss、STRASSE casefold 为 strasse）
PADDED_QUERY = " \tstrasse\n "


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

    def history(self, doc_id: int) -> list:
        """通过 history 命令读取文档修订记录。"""
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        return json.loads(result.stdout)

    def show_body(self, doc_id: int, version: int | None = None) -> bytes:
        """通过 show 命令读取（指定版本）正文。"""
        args = ["--root", str(self.kb), "show", str(doc_id)]
        if version is not None:
            args += ["--version", str(version)]
        result = run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return result.stdout

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
        return result

    def assert_empty_array(self, query: str, *extra: str) -> None:
        """断言无命中：输出 []、退出码 0、标准错误为空，且无分页信息。"""
        result = run_cli("--root", str(self.kb), "search", query, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")
        self.assertEqual(json.loads(result.stdout), [])


class ExactMatchTests(KbTestCase):
    """依次新增 Straße备忘 / STRASSE / 道路Straße / Straße 四篇文档。"""

    TITLES = ["Straße备忘", "STRASSE", "道路Straße", "Straße"]
    BODIES = [
        "Straße备忘 的正文记录。\n",
        "STRASSE 大写写法的正文。\n",
        "道路Straße 的正文记录。\n",
        "Straße 同名街道的正文。\n",
    ]

    def setUp(self) -> None:
        super().setUp()
        self.docs = [
            self.add_doc(title, body)
            for title, body in zip(self.TITLES, self.BODIES)
        ]
        self.ids = [doc["id"] for doc in self.docs]

    def entry(self, index: int, version: int = 1) -> dict:
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_four_docs_added_in_order_with_version_one(self):
        # 四篇按新增顺序取得连续 ID，初始版本均为 1，标题原样保留
        self.assertEqual(self.ids, sorted(self.ids))
        self.assertEqual(len(set(self.ids)), 4)
        for index, doc in enumerate(self.docs):
            self.assertEqual(doc["version"], 1)
            self.assertEqual(doc["title"], self.TITLES[index])
            self.assertEqual(set(doc), {"id", "version", "title"})

    def test_omitting_match_equals_explicit_contains(self):
        # 带首尾空白的小写 strasse：省略 --match 与显式 contains 结果相同，
        # 四篇全部命中并按创建所得 ID 升序返回
        default = run_cli("--root", str(self.kb), "search", PADDED_QUERY)
        explicit = run_cli(
            "--root", str(self.kb), "search", PADDED_QUERY,
            "--match", "contains",
        )
        self.assertEqual(default.returncode, 0, default.stderr.decode("utf-8"))
        self.assertEqual(explicit.returncode, 0, explicit.stderr.decode("utf-8"))
        self.assertEqual(default.stderr, b"")
        self.assertEqual(explicit.stderr, b"")
        expected = [self.entry(0), self.entry(1),
                    self.entry(2), self.entry(3)]
        self.assertEqual(json.loads(default.stdout), expected)
        self.assertEqual(json.loads(explicit.stdout), expected)
        # 两种写法连原始字节都一致
        self.assertEqual(default.stdout, explicit.stdout)

    def test_exact_returns_only_second_and_fourth_with_original_titles(self):
        # 验收核心：exact 只完整命中 STRASSE（第 2 篇）与 Straße（第 4 篇），
        # 两个标题保留原文，版本均为 1；查询词的首尾空白被去除，
        # 小写 strasse 经 casefold 与两者相等
        result = self.assert_search(
            PADDED_QUERY,
            [self.entry(1), self.entry(3)],
            "--match", "exact",
        )
        hits = json.loads(result.stdout)
        self.assertEqual([hit["id"] for hit in hits],
                         [self.ids[1], self.ids[3]])
        self.assertEqual([hit["version"] for hit in hits], [1, 1])
        self.assertEqual([hit["title"] for hit in hits],
                         ["STRASSE", "Straße"])

    def test_exact_rejects_prefix_and_inner_containment(self):
        # 第 1 篇 Straße备忘 只是以查询词开头（前缀），
        # 第 3 篇 道路Straße 只在标题中间/尾部包含查询词，
        # 两者都不是完整标题相等，exact 下不得命中
        result = run_cli(
            "--root", str(self.kb), "search", PADDED_QUERY, "--match", "exact"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        hit_ids = {hit["id"] for hit in json.loads(result.stdout)}
        self.assertNotIn(self.ids[0], hit_ids)
        self.assertNotIn(self.ids[2], hit_ids)
        # 反向对照：这两篇在 contains 下仍命中，证明排除依据是“完整相等”
        # 而非标题本身不可匹配
        contained = run_cli("--root", str(self.kb), "search", PADDED_QUERY)
        self.assertEqual(
            [hit["id"] for hit in json.loads(contained.stdout)], self.ids
        )

    def test_exact_id_and_relevance_sorts_both_return_id_ascending(self):
        # exact 下不做相关度分组：id 与 relevance 两种排序都按文档 ID 升序
        expected = [self.entry(1), self.entry(3)]
        self.assert_search(
            PADDED_QUERY, expected, "--match", "exact", "--sort", "id"
        )
        self.assert_search(
            PADDED_QUERY, expected, "--match", "exact", "--sort", "relevance"
        )
        id_result = run_cli(
            "--root", str(self.kb), "search", PADDED_QUERY,
            "--match", "exact", "--sort", "id",
        )
        rel_result = run_cli(
            "--root", str(self.kb), "search", PADDED_QUERY,
            "--match", "exact", "--sort", "relevance",
        )
        self.assertEqual(id_result.stdout, rel_result.stdout)

    def test_exact_offset_one_limit_one_returns_only_fourth(self):
        # 验收场景：ID 升序命中为第 2、4 篇，跳过 1 条再取 1 条只剩第 4 篇
        self.assert_search(
            PADDED_QUERY,
            [self.entry(3)],
            "--match", "exact", "--offset", "1", "--limit", "1",
        )
        # 两种排序下分页结果一致
        self.assert_search(
            PADDED_QUERY,
            [self.entry(3)],
            "--match", "exact", "--sort", "relevance",
            "--offset", "1", "--limit", "1",
        )

    def test_exact_offset_equal_to_hit_count_returns_empty(self):
        # exact 共 2 条命中：偏移等于命中数时返回空数组（退出码仍为 0）
        self.assert_empty_array(
            PADDED_QUERY, "--match", "exact", "--offset", "2"
        )
        self.assert_empty_array(
            PADDED_QUERY, "--match", "exact", "--offset", "2", "--limit", "1"
        )
        # 超过命中数同样为空
        self.assert_empty_array(
            PADDED_QUERY, "--match", "exact", "--offset", "9",
        )

    def test_successful_exact_output_is_pure_array_without_pagination_info(self):
        # 标准输出是仅含 id、version、title 的结果数组，不增加分页信息
        result = run_cli(
            "--root", str(self.kb), "search", PADDED_QUERY,
            "--match", "exact", "--offset", "1", "--limit", "1",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        text = result.stdout.decode("utf-8")
        self.assertTrue(text.startswith("[{"))
        self.assertEqual(text.count("{"), 1)
        for forbidden in ("total", "page", "limit", "offset", "count"):
            self.assertNotIn(forbidden, text)

    def test_update_second_doc_removes_it_from_exact_results_but_keeps_history(self):
        # 更新前：exact 命中第 2、4 篇
        self.assert_search(
            PADDED_QUERY, [self.entry(1), self.entry(3)], "--match", "exact"
        )

        # 第二篇更新为“部署指南”，正文里仍保留 STRASSE 字样，生成版本 2
        updated = self.update_doc(
            self.ids[1], "部署指南", "部署指南正文，仍保留 STRASSE 字样。\n"
        )
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["title"], "部署指南")

        # 最新正文确实含 STRASSE：若检索读取正文，第二篇就会再次命中
        latest_body = self.show_body(self.ids[1])
        self.assertIn("STRASSE", latest_body.decode("utf-8"))

        # 更新后 exact 只剩第四篇：旧标题与正文中的相同文字都不能使第二篇命中
        self.assert_search(
            PADDED_QUERY,
            [{"id": self.ids[3], "version": 1, "title": "Straße"}],
            "--match", "exact",
        )
        # 改用大写查询同样只剩第四篇（casefold 语义不变）
        self.assert_search(
            "STRASSE",
            [{"id": self.ids[3], "version": 1, "title": "Straße"}],
            "--match", "exact",
        )
        # contains 也不命中第二篇：检索既不读正文也不读历史标题
        self.assert_search(
            PADDED_QUERY,
            [self.entry(0), self.entry(2), self.entry(3)],
            "--match", "contains",
        )
        # 新标题完整匹配可查到版本 2
        self.assert_search(
            "部署指南",
            [{"id": self.ids[1], "version": 2, "title": "部署指南"}],
            "--match", "exact",
        )

        # 历史仍保留原版本：版本 1 的标题与正文都可读取且保持原样
        self.assertEqual(
            self.history(self.ids[1]),
            [
                {"version": 1, "title": "STRASSE"},
                {"version": 2, "title": "部署指南"},
            ],
        )
        self.assertEqual(
            self.show_body(self.ids[1], version=1),
            self.BODIES[1].encode("utf-8"),
        )
        # 其余三篇历史仍是版本 1
        for index in (0, 2, 3):
            self.assertEqual(
                self.history(self.ids[index]),
                [{"version": 1, "title": self.TITLES[index]}],
            )

        # 查询前后索引、正文文件内容与历史记录相同（检索只读）
        before_files = snapshot(self.kb)
        before_history = {doc_id: self.history(doc_id) for doc_id in self.ids}
        for extra in [
            ("--match", "exact"),
            ("--match", "exact", "--sort", "id"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--offset", "1", "--limit", "1"),
            ("--match", "exact", "--offset", "2"),
            ("--match", "contains"),
        ]:
            result = run_cli(
                "--root", str(self.kb), "search", PADDED_QUERY, *extra
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")
        # 无命中的 exact 查询同样只读
        no_hit = run_cli(
            "--root", str(self.kb), "search", "gibtsnicht", "--match", "exact"
        )
        self.assertEqual(no_hit.returncode, 0)
        self.assertEqual(no_hit.stdout, b"[]\n")
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(
            {doc_id: self.history(doc_id) for doc_id in self.ids},
            before_history,
        )

    def test_exact_queries_do_not_modify_store(self):
        # 更新发生前：一批成功 exact 查询（含分页）前后，
        # 索引、正文文件与各文档历史逐一不变
        before_files = snapshot(self.kb)
        before_history = {doc_id: self.history(doc_id) for doc_id in self.ids}
        for extra in [
            ("--match", "exact"),
            ("--match", "exact", "--sort", "id"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--limit", "1"),
            ("--match", "exact", "--offset", "1"),
            ("--match", "exact", "--offset", "1", "--limit", "1"),
            ("--match", "exact", "--offset", "2"),
        ]:
            result = run_cli(
                "--root", str(self.kb), "search", PADDED_QUERY, *extra
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(
            {doc_id: self.history(doc_id) for doc_id in self.ids},
            before_history,
        )

    def test_same_exact_query_repeated_runs_byte_identical(self):
        for extra in [
            ("--match", "exact"),
            ("--match", "exact", "--sort", "relevance"),
            ("--match", "exact", "--offset", "1", "--limit", "1"),
        ]:
            first = run_cli(
                "--root", str(self.kb), "search", PADDED_QUERY, *extra
            )
            second = run_cli(
                "--root", str(self.kb), "search", PADDED_QUERY, *extra
            )
            self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
            self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8"))
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(first.stderr, b"")
            self.assertEqual(second.stderr, b"")


class ExactEmptyResultTests(KbTestCase):
    """exact 无命中与根目录缺失时：输出 []、退出码 0、不创建目录或索引。"""

    def test_no_hit_returns_empty_array(self):
        self.add_doc("Straße备忘", "正文。\n")
        # 完整标题不等于任何最新标题：前缀不算完整命中
        self.assert_empty_array("strasse", "--match", "exact")
        # 完全陌生的查询词同样为空，分页参数不改变空结果约定
        self.assert_empty_array(
            "gibtsnicht", "--match", "exact", "--offset", "1", "--limit", "1"
        )

    def test_missing_root_returns_empty_and_creates_nothing(self):
        missing = self.tmp / "no_such_dir"
        result = run_cli(
            "--root", str(missing), "search", "strasse", "--match", "exact"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")
        self.assertFalse(missing.exists())

        # 带合法分页参数时同样不创建任何目录或索引
        paged = run_cli(
            "--root", str(missing), "search", "strasse", "--match", "exact",
            "--offset", "1", "--limit", "1",
        )
        self.assertEqual(paged.returncode, 0)
        self.assertEqual(paged.stdout, b"[]\n")
        self.assertFalse(missing.exists())

    def test_dir_without_index_returns_empty_and_creates_nothing(self):
        self.kb.mkdir()
        self.assert_empty_array("strasse", "--match", "exact")
        # 不补建索引或正文目录
        self.assertEqual(list(self.kb.iterdir()), [])


class ExactMatchArgumentErrorTests(KbTestCase):
    """--match 参数错误约定：退出码 2、标准输出为空、标准错误说明参数问题。"""

    def assert_match_error(self, args: tuple, root: Path | None = None) -> None:
        root = self.kb if root is None else root
        result = run_cli("--root", str(root), "search", "strasse", *args)
        self.assertEqual(result.returncode, 2,
                         f"退出码应为 2: {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        # 标准错误指出是 --match 参数问题，且不含调用栈
        self.assertIn("--match", err)
        self.assertNotIn("Traceback", err)

    def test_match_missing_value(self):
        self.assert_match_error(("--match",))

    def test_match_unknown_value(self):
        result = run_cli(
            "--root", str(self.kb), "search", "strasse",
            "--match", "fuzzy",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        err = result.stderr.decode("utf-8")
        self.assertIn("--match", err)
        self.assertIn("invalid choice", err)
        self.assertNotIn("Traceback", err)

    def test_match_errors_even_when_root_missing(self):
        # 即使根目录不存在，参数解析也先于存储访问：缺值与未知取值都以
        # 退出码 2 失败，标准输出为空，标准错误说明参数问题且无调用栈，
        # 不输出空数组伪装成功，也不创建目录或索引
        missing = self.tmp / "no_such_dir"
        for args in [("--match",), ("--match", "fuzzy")]:
            with self.subTest(args=args):
                self.assert_match_error(args, root=missing)
        self.assertFalse(missing.exists())

    def test_match_argument_error_creates_nothing(self):
        # 参数错误在访问存储之前失败，不创建知识库目录
        self.assert_match_error(("--match",))
        self.assert_match_error(("--match", "fuzzy"))
        self.assertFalse(self.kb.exists())


if __name__ == "__main__":
    unittest.main()
