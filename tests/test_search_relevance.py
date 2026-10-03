"""search 相关性排序的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
用 add 返回的文档 ID 组织预期结果。测试数据放在独立临时目录，执行结束
自动清理，可离线重复运行。

发现与执行：在仓库根目录运行 ``python -m unittest discover``（或
``python -m unittest discover -s tests``）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 包所在目录，作为子进程 cwd

# 四篇检索用文档的标题，按新增顺序
TITLES = ["发布流程", "预发布检查", "发布", "发布记录"]
CASEFOLD_TITLE = "Straße"
UPDATED_TITLE = "归档说明"


def run_cli(kb_dir, *args):
    """以仓库根目录为 cwd 调用公开入口，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, "-m", "knowledge_base", "--root", str(kb_dir), *args],
        capture_output=True,
        cwd=ROOT,
    )


def snapshot(root):
    """递归快照目录内容（相对路径 + 文件字节），用于断言检索不产生副作用。"""
    if not root.exists():
        return None
    return sorted(
        (str(p.relative_to(root)), p.read_bytes() if p.is_file() else b"<dir>")
        for p in root.rglob("*")
    )


class SearchCliCase(unittest.TestCase):
    """提供临时目录、文档准备与检索断言的基类。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kb_search_test_"))
        cls.kb = cls.tmp / "kb"
        cls._body_counter = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def add_doc(cls, title, body):
        """通过 add 命令新增文档，返回 (id, version)；失败即中断测试。"""
        cls._body_counter += 1
        body_file = cls.tmp / f"body_{cls._body_counter}.md"
        body_file.write_text(body, encoding="utf-8")
        result = run_cli(cls.kb, "add", "--title", title, "--file", str(body_file))
        assert result.returncode == 0, f"add {title!r} 失败: {result.stderr!r}"
        payload = json.loads(result.stdout.decode("utf-8"))
        return payload["id"], payload["version"]

    @classmethod
    def update_doc(cls, doc_id, title, body):
        """通过 update 命令更新文档，返回新版本号；失败即中断测试。"""
        cls._body_counter += 1
        body_file = cls.tmp / f"body_{cls._body_counter}.md"
        body_file.write_text(body, encoding="utf-8")
        result = run_cli(
            cls.kb, "update", str(doc_id), "--title", title, "--file", str(body_file)
        )
        assert result.returncode == 0, f"update {doc_id} 失败: {result.stderr!r}"
        return json.loads(result.stdout.decode("utf-8"))["version"]

    def search(self, *args):
        """执行 search 并断言成功退出、标准错误为空，返回解析后的 JSON。"""
        result = run_cli(self.kb, "search", *args)
        self.assertEqual(
            result.returncode, 0, f"search {args}: 退出码异常\n{result.stderr!r}"
        )
        self.assertEqual(
            result.stderr, b"", f"search {args}: 标准错误应为空: {result.stderr!r}"
        )
        return json.loads(result.stdout.decode("utf-8"))

    @staticmethod
    def entry(doc_id, version, title):
        return {"id": doc_id, "version": version, "title": title}


class SearchOrderingTest(SearchCliCase):
    """默认/id/relevance 排序、结果字段、Unicode 分组与查询词空白处理。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 依次新增：发布流程、预发布检查、发布、发布记录
        cls.docs = {}
        for title in TITLES:
            doc_id, version = cls.add_doc(title, f"{title}的正文。\n")
            cls.docs[title] = (doc_id, version)
        cls.casefold_doc = cls.add_doc(CASEFOLD_TITLE, "德国街道命名规范。\n")

    def test_add_assigned_sequential_ids(self):
        ids = [self.docs[title][0] for title in TITLES]
        self.assertEqual(ids, [1, 2, 3, 4])
        self.assertTrue(all(version == 1 for _, version in self.docs.values()))

    def expected(self, *titles):
        return [self.entry(*self.docs[title], title) for title in titles]

    def test_default_sort_is_id_order(self):
        hits = self.search("发布")
        self.assertEqual(hits, self.expected("发布流程", "预发布检查", "发布", "发布记录"))

    def test_explicit_id_sort_matches_default(self):
        hits = self.search("发布", "--sort", "id")
        self.assertEqual(hits, self.expected("发布流程", "预发布检查", "发布", "发布记录"))

    def test_relevance_sort_groups(self):
        """完全相等优先、前缀其次、其余包含最后，同组按 ID 升序。"""
        hits = self.search("发布", "--sort", "relevance")
        self.assertEqual(
            hits,
            self.expected("发布", "发布流程", "发布记录", "预发布检查"),
        )

    def test_result_entries_have_exact_fields(self):
        """每条结果只含 id、version、title。"""
        for hit in self.search("发布"):
            self.assertEqual(set(hit), {"id", "version", "title"})
            self.assertIsInstance(hit["id"], int)
            self.assertIsInstance(hit["version"], int)
            self.assertIsInstance(hit["title"], str)

    def test_unicode_casefold_equal_group(self):
        """STRASSE 与 Straße 在 casefold 下相等，标题保留原文。"""
        doc_id, version = self.casefold_doc
        expected = [self.entry(doc_id, version, CASEFOLD_TITLE)]
        for query in ("STRASSE", "straße", "Straße"):
            with self.subTest(query=query):
                self.assertEqual(self.search(query), expected)
                self.assertEqual(self.search(query, "--sort", "relevance"), expected)

    def test_surrounding_whitespace_stripped(self):
        hits = self.search("  发布\t")
        self.assertEqual(hits, self.expected("发布流程", "预发布检查", "发布", "发布记录"))

    def test_inner_space_is_literal(self):
        """内部空格按字面保留：'发 布' 不匹配任何不含该字面空格的标题。"""
        self.assertEqual(self.search("发 布"), [])
        self.assertEqual(self.search("发布 流程"), [])

    def test_search_does_not_modify_storage_or_history(self):
        """成功与失败检索前后，文件内容与修订记录保持不变。"""
        before = snapshot(self.kb)
        history_before = {
            title: run_cli(self.kb, "history", str(self.docs[title][0])).stdout
            for title in TITLES
        }
        self.search("发布", "--sort", "relevance")
        failed = run_cli(self.kb, "search", "   ")
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(snapshot(self.kb), before)
        for title in TITLES:
            self.assertEqual(
                run_cli(self.kb, "history", str(self.docs[title][0])).stdout,
                history_before[title],
            )


class SearchAfterUpdateTest(SearchCliCase):
    """更新标题后，旧标题与正文均不能使文档命中，新标题返回最新版本。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.docs = {}
        for title in TITLES:
            doc_id, version = cls.add_doc(title, f"{title}的正文。\n")
            cls.docs[title] = (doc_id, version)
        # 第三篇（发布）改名为归档说明，正文仍含“发布”
        cls.updated_id = cls.docs["发布"][0]
        cls.updated_version = cls.update_doc(
            cls.updated_id, UPDATED_TITLE, "发布后请填写归档记录。\n"
        )

    def test_update_produced_next_version(self):
        self.assertEqual(self.updated_version, 2)

    def test_old_title_and_body_no_longer_match(self):
        """搜索“发布”只命中第一、第四、第二篇（relevance 顺序）。"""
        expected = [
            self.entry(*self.docs["发布流程"], "发布流程"),
            self.entry(*self.docs["发布记录"], "发布记录"),
            self.entry(*self.docs["预发布检查"], "预发布检查"),
        ]
        self.assertEqual(self.search("发布", "--sort", "relevance"), expected)
        by_id = [expected[0], expected[2], expected[1]]  # ID 升序：1、2、4
        self.assertEqual(self.search("发布"), by_id)
        self.assertEqual(self.search("发布", "--sort", "id"), by_id)

    def test_new_title_returns_latest_version(self):
        hits = self.search("归档")
        self.assertEqual(
            hits, [self.entry(self.updated_id, self.updated_version, UPDATED_TITLE)]
        )

    def test_history_keeps_both_revisions(self):
        result = run_cli(self.kb, "history", str(self.updated_id))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [
                {"version": 1, "title": "发布"},
                {"version": 2, "title": UPDATED_TITLE},
            ],
        )


class SearchErrorTest(SearchCliCase):
    """QUERY 缺失/空白、--sort 缺值/非法：退出码 2，标准输出为空，数据不变。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.doc = cls.add_doc("发布流程", "正文。\n")

    def assert_usage_error(self, args, reason, label):
        before = snapshot(self.kb)
        result = run_cli(self.kb, *args)
        self.assertEqual(result.returncode, 2, f"{label}: 退出码 {result.returncode}")
        self.assertEqual(
            result.stdout, b"", f"{label}: 标准输出应为空: {result.stdout!r}"
        )
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应有堆栈\n{err}")
        self.assertIn(reason, err, f"{label}: 标准错误应说明原因 {reason!r}\n{err}")
        self.assertEqual(snapshot(self.kb), before, f"{label}: 失败检索改变了知识库")

    def test_missing_query(self):
        self.assert_usage_error(("search",), "QUERY", "QUERY 缺失")

    def test_blank_query(self):
        self.assert_usage_error(("search", "   "), "不能为空", "QUERY 仅为空白")

    def test_sort_missing_value(self):
        self.assert_usage_error(
            ("search", "发布", "--sort"), "--sort", "--sort 缺值"
        )

    def test_sort_invalid_value(self):
        self.assert_usage_error(
            ("search", "发布", "--sort", "newest"), "--sort", "--sort 非法值"
        )


class SearchEmptyResultTest(SearchCliCase):
    """无命中、根目录不存在、目录尚无索引：返回 []，退出码 0，不创建任何内容。"""

    def test_no_hits_returns_empty_array(self):
        self.add_doc("发布流程", "正文。\n")
        self.assertEqual(self.search("不存在的词"), [])

    def test_missing_root_returns_empty_and_creates_nothing(self):
        missing = self.tmp / "no_such_root"
        self.assertFalse(missing.exists())
        result = run_cli(missing, "search", "发布")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), [])
        self.assertFalse(missing.exists(), "检索不应创建根目录")

    def test_dir_without_index_returns_empty_and_creates_nothing(self):
        empty_dir = self.tmp / "empty_root"
        empty_dir.mkdir()
        result = run_cli(empty_dir, "search", "发布")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), [])
        self.assertEqual(list(empty_dir.iterdir()), [], "检索不应创建索引或目录")


if __name__ == "__main__":
    unittest.main()
