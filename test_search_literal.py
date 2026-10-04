"""形似通配符查询词按普通字符字面匹配的回归测试。

README 约定：``%``、``_``、``*``、``[``、``]`` 在 search 查询词中均为普通
字符，按字面子串匹配，不解释为正则、glob 或 SQL LIKE 通配符。本文件通过
公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下行为，防止形似通配符的查询扩大命中范围：

- 依次新增 前置100%指南 / 100%指南 / 100X指南（版本均为 1，正文为空）后，
  查询 ``100%`` 默认排序只返回前两篇并按文档 ID 升序；
  ``--sort relevance --offset 1 --limit 1`` 只返回 前置100%指南，
  100X指南 不进入结果也不占据分页名额；
- 对 ``_``、``*``、``[``、``]`` 各设含原字符的标题与不含该字符的相似
  标题，只有字面包含查询词的文档被返回；单独查询 ``[`` 或 ``]`` 是合法
  查询，不解释为字符组，也不报语法错误；
- 查询某符号而所有最新标题都不含它时输出 ``[]``、退出码 0、标准错误为空；
  有命中时同样保持退出码 0 与空标准错误；
- 查询词缺失或去除首尾空白后为空时退出码 2、标准输出为空、标准错误说明
  原因，与合法符号查询不混淆；
- 检索前后知识库目录条目与已有文件字节不变，不新增修订，不改写正文或
  索引；
- 已有的 Unicode casefold 匹配、只检索最新标题、排序后先偏移再限制条数
  的行为在符号查询词下继续保留。

结果仍为纯 JSON 数组，每项仅含 id、version、title，标题保持原样，预期
ID 一律来自 add 命令的返回值。每个用例使用独立临时目录与 UTF-8 正文，
结束后清理自身数据，可离线重复运行，不依赖已有文档。
执行方式：``python -m unittest test_search_literal`` 或
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
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_literal_test_"))
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
        同时固定输出为纯 JSON 数组，每项只含 id、version、title 三个字段。
        """
        result = run_cli("--root", str(self.kb), "search", query, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        hits = json.loads(result.stdout)
        self.assertIsInstance(hits, list)
        self.assertEqual(hits, expected)
        for hit in hits:
            self.assertEqual(set(hit), {"id", "version", "title"})


class PercentQueryTests(KbTestCase):
    """核心样例：前置100%指南 / 100%指南 / 100X指南，查询 100%。

    三篇文档初始版本均为 1，正文为空；``%`` 按普通字符匹配，
    100X指南 不得因形似通配符的语义进入结果。
    """

    TITLES = ["前置100%指南", "100%指南", "100X指南"]

    def setUp(self) -> None:
        super().setUp()
        self.docs = [self.add_doc(title, "") for title in self.TITLES]
        self.ids = [d["id"] for d in self.docs]
        # add 返回值本身固定了初始版本与标题原样
        for doc, title in zip(self.docs, self.TITLES):
            self.assertEqual(doc["version"], 1)
            self.assertEqual(doc["title"], title)

    def entry(self, index: int) -> dict:
        return {"id": self.ids[index], "version": 1,
                "title": self.TITLES[index]}

    def test_default_sort_returns_literal_percent_hits_by_id(self):
        # 默认排序只返回前两篇，按文档 ID 升序；100X指南 不命中
        self.assert_search("100%", [self.entry(0), self.entry(1)])

    def test_explicit_id_sort_matches_default(self):
        self.assert_search(
            "100%", [self.entry(0), self.entry(1)], "--sort", "id"
        )

    def test_relevance_full_order_excludes_100x(self):
        # relevance 完整顺序：完全相等的 100%指南 最前，
        # 其余包含的 前置100%指南 其次；100X指南 不在结果中
        self.assert_search(
            "100%",
            [self.entry(1), self.entry(0)],
            "--sort", "relevance",
        )

    def test_relevance_offset_1_limit_1_returns_only_prefixed_title(self):
        # --sort relevance --offset 1 --limit 1 只返回 前置100%指南；
        # 100X指南 不能进入结果或占据分页名额（否则此处会偏移到它或落空）
        self.assert_search(
            "100%",
            [self.entry(0)],
            "--sort", "relevance", "--offset", "1", "--limit", "1",
        )

    def test_id_sort_offset_then_limit(self):
        # 排序后先偏移再限制条数：id 升序下跳过第一篇后只剩 100%指南
        self.assert_search(
            "100%", [self.entry(1)], "--offset", "1", "--limit", "1"
        )

    def test_result_is_plain_json_array_with_verbatim_titles(self):
        result = run_cli("--root", str(self.kb), "search", "100%")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        text = result.stdout.decode("utf-8")
        self.assertTrue(text.startswith("["), text)
        self.assertTrue(text.endswith("]\n"), text)
        hits = json.loads(text)
        self.assertEqual([h["title"] for h in hits],
                         ["前置100%指南", "100%指南"])
        for hit in hits:
            self.assertEqual(set(hit), {"id", "version", "title"})

    def test_percent_query_does_not_modify_store(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)

        for args in [
            ("100%",),
            ("100%", "--sort", "relevance", "--offset", "1", "--limit", "1"),
            ("100%", "--offset", "1", "--limit", "1"),
            ("%",),  # 单独的 % 同样按字面匹配，命中两篇含 % 的标题
        ]:
            result = run_cli("--root", str(self.kb), "search", *args)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertEqual(result.stderr, b"")

        # 目录条目与已有文件字节不变，不新增修订，不改写正文或索引
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)


class LiteralSymbolTests(KbTestCase):
    """_、*、[、] 均按普通字符匹配，相似但不含该字符的标题不命中。"""

    def check_symbol(self, symbol: str, with_title: str,
                     without_title: str) -> None:
        """新增含原字符与不含该字符的相似标题，断言只有字面包含者命中。"""
        with_doc = self.add_doc(with_title, f"{with_title} 的正文。\n")
        without_doc = self.add_doc(without_title, f"{without_title} 的正文。\n")

        expected = [{"id": with_doc["id"], "version": 1, "title": with_title}]
        # 默认排序与 relevance 排序都只返回字面包含查询词的文档
        self.assert_search(symbol, expected)
        self.assert_search(symbol, expected, "--sort", "relevance")
        # 相似标题不因通配语义命中：完整结果只有一条，分页也轮不到它
        self.assert_search(symbol, expected, "--offset", "0", "--limit", "5")
        self.assert_search(symbol, [], "--offset", "1")
        # 不含该字符的相似标题本身可作为查询词命中它自己，证明文档存在
        self.assert_search(
            without_title,
            [{"id": without_doc["id"], "version": 1, "title": without_title}],
        )

    def test_underscore_is_literal(self):
        self.check_symbol("_", "部署_手册", "部署手册")

    def test_asterisk_is_literal(self):
        self.check_symbol("*", "导出*备注", "导出备注")

    def test_open_bracket_is_literal(self):
        self.check_symbol("[", "索引[附录", "索引附录")

    def test_close_bracket_is_literal(self):
        self.check_symbol("]", "索引]附录", "索引附录")

    def test_lone_open_bracket_is_legal_query(self):
        # 单独查询 [ 是合法查询，不解释为字符组开头，也不报语法错误
        doc = self.add_doc("附录[甲", "附录甲的正文。\n")
        self.add_doc("附录乙", "附录乙的正文。\n")
        self.assert_search(
            "[", [{"id": doc["id"], "version": 1, "title": "附录[甲"}]
        )

    def test_lone_close_bracket_is_legal_query(self):
        # 单独查询 ] 同样合法，不报语法错误
        doc = self.add_doc("附录]甲", "附录甲的正文。\n")
        self.add_doc("附录乙", "附录乙的正文。\n")
        self.assert_search(
            "]", [{"id": doc["id"], "version": 1, "title": "附录]甲"}]
        )

    def test_bracket_pair_is_literal_not_character_class(self):
        # [甲] 整体按字面匹配，不解释为匹配单个字符 甲 的字符组
        doc = self.add_doc("图例[甲]说明", "图例正文。\n")
        other = self.add_doc("图例甲说明", "不含括号的正文。\n")
        self.assert_search(
            "[甲]", [{"id": doc["id"], "version": 1, "title": "图例[甲]说明"}]
        )
        # 若被当作字符组，"甲" 会命中两篇；字面匹配下只命中含原字符者
        self.assert_search(
            "甲",
            [
                {"id": doc["id"], "version": 1, "title": "图例[甲]说明"},
                {"id": other["id"], "version": 1, "title": "图例甲说明"},
            ],
        )


class SymbolQueryOutcomeTests(KbTestCase):
    """符号查询的退出码与输出通道约定。"""

    def test_no_hit_symbol_query_outputs_empty_array(self):
        # 所有最新标题都不含该符号：输出 []、退出码 0、标准错误为空
        self.add_doc("普通标题", "普通正文。\n")
        self.add_doc("另一篇文档", "另一篇正文。\n")
        for symbol in ["%", "_", "*", "[", "]"]:
            with self.subTest(symbol=symbol):
                result = run_cli(
                    "--root", str(self.kb), "search", symbol
                )
                self.assertEqual(
                    result.returncode, 0, result.stderr.decode("utf-8")
                )
                self.assertEqual(result.stderr, b"")
                self.assertEqual(result.stdout, b"[]\n")

    def test_hit_symbol_query_keeps_clean_channels(self):
        # 有命中时同样退出码 0、标准错误为空
        self.add_doc("含%标题", "正文。\n")
        result = run_cli("--root", str(self.kb), "search", "%")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(len(json.loads(result.stdout)), 1)

    def test_missing_query_is_argument_error_not_symbol_query(self):
        # 查询词缺失：退出码 2、标准输出为空、标准错误说明原因
        self.add_doc("含%标题", "正文。\n")
        result = run_cli("--root", str(self.kb), "search")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn("QUERY", result.stderr.decode("utf-8"))
        # 与合法符号查询不混淆：同一知识库下符号查询正常命中
        ok = run_cli("--root", str(self.kb), "search", "%")
        self.assertEqual(ok.returncode, 0, ok.stderr.decode("utf-8"))
        self.assertEqual(len(json.loads(ok.stdout)), 1)

    def test_blank_query_is_argument_error_not_symbol_query(self):
        # 去除首尾空白后为空的查询词：退出码 2、标准输出为空、标准错误说明原因
        self.add_doc("含%标题", "正文。\n")
        for blank in ["   ", "\t\n "]:
            with self.subTest(blank=blank):
                result = run_cli(
                    "--root", str(self.kb), "search", blank
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertIn(
                    "QUERY 去除首尾空白后不能为空",
                    result.stderr.decode("utf-8"),
                )
        # 合法符号查询不受参数错误用例影响
        ok = run_cli("--root", str(self.kb), "search", "%")
        self.assertEqual(ok.returncode, 0, ok.stderr.decode("utf-8"))
        self.assertEqual(len(json.loads(ok.stdout)), 1)


class SymbolSemanticsPreservedTests(KbTestCase):
    """符号查询词下既有检索语义继续保留。"""

    def test_casefold_matching_with_symbol_query(self):
        # Unicode casefold 匹配对含符号的查询词同样生效
        doc = self.add_doc("ABC_DEF 指南", "正文。\n")
        expected = [{"id": doc["id"], "version": 1, "title": "ABC_DEF 指南"}]
        self.assert_search("abc_def", expected)
        self.assert_search("ABC_def", expected, "--sort", "relevance")

    def test_only_latest_titles_are_searched(self):
        # 旧标题含 _ 的文档更新为不含 _ 的标题后，符号查询不再命中它
        doc = self.add_doc("旧_标题", "正文提到 _ 符号。\n")
        updated = self.update_doc(doc["id"], "普通标题", "正文仍提到 _ 符号。\n")
        self.assertEqual(updated["version"], 2)

        # 最新标题与正文都不让旧 _ 进入结果；历史标题与正文均不产生命中
        result = run_cli("--root", str(self.kb), "search", "_")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")
        # 新标题查询返回最新版本
        self.assert_search(
            "普通标题",
            [{"id": doc["id"], "version": 2, "title": "普通标题"}],
        )
        # 修订历史完整保留，检索不产生新修订
        self.assertEqual(
            self.histories([doc["id"]])[doc["id"]],
            [{"version": 1, "title": "旧_标题"},
             {"version": 2, "title": "普通标题"}],
        )

    def test_offset_applies_before_limit_with_symbol_query(self):
        # 排序完成后先偏移再限制条数，偏移统计命中条数而非文档 ID
        docs = [
            self.add_doc(title, f"{title} 的正文。\n")
            for title in ["a_b 一", "a_b 二", "a_b 三"]
        ]
        expected_all = [
            {"id": d["id"], "version": 1, "title": t}
            for d, t in zip(docs, ["a_b 一", "a_b 二", "a_b 三"])
        ]
        self.assert_search("a_b", expected_all)
        self.assert_search("a_b", expected_all[1:], "--offset", "1")
        self.assert_search(
            "a_b", expected_all[1:2], "--offset", "1", "--limit", "1"
        )
        self.assert_search("a_b", [], "--offset", "3")


if __name__ == "__main__":
    unittest.main()
