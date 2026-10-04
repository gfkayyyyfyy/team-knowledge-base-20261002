"""search 子命令中形似通配符符号的字面子串匹配回归测试。

README 约定 ``%``、``_``、``*``、``[``、``]`` 在检索中均按普通字符处理，
不是 SQL LIKE、shell glob 或正则字符组。本模块通过公开入口
``python -m knowledge_base`` 观察退出码、标准输出与标准错误，固定：

- 查询 ``100%`` 时，只有最新标题字面子串包含 ``100%`` 的文档命中，
  形似的 ``100X指南`` 既不进入结果，也不占据 relevance 排序下的分页名额；
  默认排序按文档 ID 升序，``--sort relevance --offset 1 --limit 1``
  在“前缀组 + 包含组”两篇命中上跳过后只返回“前置100%指南”；
- ``_`` 不充当单字符通配：``进度A跟踪`` 不因查询 ``_`` 或 ``进度_跟``
  命中；``*`` 不充当 glob：即使知识库内有多篇文档，查询 ``*`` 也只返回
  标题字面子串含 ``*`` 的那一篇；
- 单独查询 ``[`` 或 ``]`` 是合法查询，退出码 0，既不解释为字符组也不报
  语法错误；``[甲]``、``[清]`` 这类写法同样按字面整体匹配，不会退化为
  “匹配其中任一字符”；
- 查询某个所有最新标题都不含的符号时输出 ``[]``，退出码 0、标准错误为空；
  有命中时同样退出码 0、标准错误为空，输出纯 JSON 数组，每项仅含
  id、version、title，标题（含符号）原样保留，预期 ID 一律取自 add 返回值；
- 查询词缺失或去除首尾空白后为空仍按参数错误处理（退出码 2、标准输出为空、
  标准错误说明原因），不与 ``[``、``]`` 等合法单字符符号查询混淆；
- 上述查询（含无命中查询与参数错误）前后，知识库目录条目与已有文件字节
  逐一不变，不新增修订，不改写空正文或索引；同一次查询重复执行结果字节一致；
- 既有的 Unicode casefold 匹配与只检索最新标题的语义在符号场景下继续成立。

每个用例使用独立临时目录与 UTF-8 正文（本模块正文均为空），结束后清理，
不依赖已有文档或联网，可离线重复运行。
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
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_search_literal_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, body: str) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_text(body, encoding="utf-8")
        return body_file

    def add_doc(self, title: str, body: str = "") -> dict:
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

    def assert_empty_hits(self, query: str, *extra: str) -> None:
        """断言无命中：输出 []、退出码 0、标准错误为空。"""
        result = run_cli("--root", str(self.kb), "search", query, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")
        self.assertEqual(json.loads(result.stdout), [])


class LiteralPercentTests(KbTestCase):
    """依次新增“前置100%指南”“100%指南”“100X指南”（版本均为 1、正文为空）。"""

    TITLES = ["前置100%指南", "100%指南", "100X指南"]

    def setUp(self) -> None:
        super().setUp()
        self.docs = [self.add_doc(title, "") for title in self.TITLES]
        self.ids = [doc["id"] for doc in self.docs]

    def entry(self, index: int, version: int = 1) -> dict:
        return {"id": self.ids[index], "version": version,
                "title": self.TITLES[index]}

    def test_add_returns_version_one_with_original_titles(self):
        # 三篇文档初始版本均为 1，标题含 % / X 原样返回，ID 来自新增返回值
        for index, doc in enumerate(self.docs):
            self.assertEqual(doc["version"], 1)
            self.assertEqual(doc["title"], self.TITLES[index])
            self.assertEqual(set(doc), {"id", "version", "title"})
        self.assertEqual(self.ids, sorted(self.ids))

    def test_default_sort_returns_only_literal_percent_hits_in_id_order(self):
        # 验收核心：100% 只按字面子串命中前两篇，100X指南 不在结果中；
        # 默认排序按文档 ID 升序
        self.assert_search("100%", [self.entry(0), self.entry(1)])

    def test_explicit_id_sort_same_as_default(self):
        self.assert_search(
            "100%", [self.entry(0), self.entry(1)], "--sort", "id"
        )

    def test_relevance_full_order_excludes_non_matching_title(self):
        # “100%指南”以查询词开头（前缀组），“前置100%指南”为其余包含组，
        # 100X指南 不命中；relevance 完整顺序只有两篇
        self.assert_search(
            "100%",
            [self.entry(1), self.entry(0)],
            "--sort", "relevance",
        )

    def test_relevance_offset_one_limit_one_returns_prefixed_doc(self):
        # 验收场景：跳过前缀组的“100%指南”后只返回“前置100%指南”
        self.assert_search(
            "100%",
            [self.entry(0)],
            "--sort", "relevance", "--offset", "1", "--limit", "1",
        )

    def test_non_matching_title_cannot_enter_results_or_take_page_slot(self):
        # 默认排序即使放宽上限也只有两篇，100X指南 不补位
        self.assert_search(
            "100%", [self.entry(0), self.entry(1)], "--limit", "9"
        )
        # relevance 下命中总数为 2：偏移 2 已越界，输出 []。
        # 若 100X指南 被 % 通配误判为命中，偏移 2 反而会返回它。
        self.assert_empty_hits(
            "100%", "--sort", "relevance", "--offset", "2", "--limit", "1"
        )
        # 直接按字面查询形似标题本身可以命中，证明区分依据是字面包含
        # 而非把 % 当任意序列：100% 查不到它，100X 才能查到它
        self.assert_search("100X", [self.entry(2)])
        # “100%指”仍只命中前两篇；通配写法“100_指”谁都不命中
        self.assert_search("100%指", [self.entry(0), self.entry(1)])
        self.assert_empty_hits("100_指")

    def test_titles_kept_verbatim_in_json(self):
        result = run_cli("--root", str(self.kb), "search", "100%")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        hits = json.loads(result.stdout)
        self.assertEqual([hit["title"] for hit in hits],
                         ["前置100%指南", "100%指南"])
        # 输出是纯 JSON 数组，不含总数等元数据，% 不被转义
        text = result.stdout.decode("utf-8")
        self.assertTrue(text.startswith("[{"))
        self.assertIn("100%指南", text)
        self.assertNotIn("100X指南", text)

    def test_absent_symbol_returns_empty_array(self):
        # 三篇标题均不含 # ：输出 []、退出码 0、标准错误为空
        self.assert_empty_hits("#")
        self.assert_empty_hits("#", "--sort", "relevance")
        self.assert_empty_hits("#", "--offset", "1", "--limit", "1")

    def test_same_query_repeated_runs_byte_identical(self):
        for query, extra in [
            ("100%", ()),
            ("100%", ("--sort", "relevance", "--offset", "1", "--limit", "1")),
            ("#", ()),
        ]:
            first = run_cli("--root", str(self.kb), "search", query, *extra)
            second = run_cli("--root", str(self.kb), "search", query, *extra)
            self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
            self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8"))
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(first.stderr, b"")
            self.assertEqual(second.stderr, b"")

    def test_queries_do_not_modify_store_or_empty_bodies(self):
        before_files = snapshot(self.kb)
        before_history = self.histories(self.ids)
        # 三篇正文均为空，查询前后 show 输出都应为空字节
        for doc_id in self.ids:
            show = run_cli("--root", str(self.kb), "show", str(doc_id))
            self.assertEqual(show.returncode, 0, show.stderr.decode("utf-8"))
            self.assertEqual(show.stdout, b"")

        for extra in [
            (),
            ("--sort", "id"),
            ("--sort", "relevance"),
            ("--sort", "relevance", "--offset", "1", "--limit", "1"),
            ("--offset", "2", "--limit", "1"),
            ("--limit", "9"),
        ]:
            result = run_cli("--root", str(self.kb), "search", "100%", *extra)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        # 无命中的符号查询同样只读
        self.assert_empty_hits("#")

        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(self.ids), before_history)

    def test_latest_title_only_semantics_preserved_with_literal_symbol(self):
        # 更新第三篇为含 100% 的新标题（版本 2，非空 UTF-8 正文）：
        # 只检索最新标题，旧标题 100X指南 不再可查，新标题以版本 2 命中
        updated = self.update_doc(self.ids[2], "100%补遗", "补充正文。\n")
        self.assertEqual(updated["version"], 2)

        self.assert_search(
            "100%",
            [self.entry(0), self.entry(1),
             {"id": self.ids[2], "version": 2, "title": "100%补遗"}],
        )
        # relevance 下“100%指南”“100%补遗”同属前缀组（按 ID 升序），
        # “前置100%指南”为其余包含组
        self.assert_search(
            "100%",
            [self.entry(1),
             {"id": self.ids[2], "version": 2, "title": "100%补遗"},
             self.entry(0)],
            "--sort", "relevance",
        )
        # 旧最新标题 100X指南 已不可经检索得到（历史标题不参与匹配）
        self.assert_empty_hits("100X")
        self.assertEqual(
            self.histories([self.ids[2]])[self.ids[2]],
            [
                {"version": 1, "title": "100X指南"},
                {"version": 2, "title": "100%补遗"},
            ],
        )


class LiteralSymbolTests(KbTestCase):
    """_、*、[、] 字面值与通配符/字符组形态的对照测试。

    每个符号配一对标题：一篇字面子串含该符号，另一篇把符号替换成普通
    字母 A 作为不含该字符的相似标题；另加“备注[甲]项 / 备注甲项”一对，
    专门对照字符组写法。
    """

    # (符号, 含符号标题, 不含符号的相似标题)
    SYMBOL_CASES = [
        ("_", "进度_跟踪", "进度A跟踪"),
        ("*", "重点*事项", "重点A事项"),
        ("[", "待办[清单", "待办A清单"),
        ("]", "清单]归档", "清单A归档"),
    ]
    PAIR_HIT_TITLE = "备注[甲]项"
    PAIR_MISS_TITLE = "备注甲项"

    def setUp(self) -> None:
        super().setUp()
        self.entries = {}  # 标题 -> {"id", "version", "title"}
        for _symbol, hit_title, miss_title in self.SYMBOL_CASES:
            self.entries[hit_title] = self.add_doc(hit_title, "")
            self.entries[miss_title] = self.add_doc(miss_title, "")
        self.entries[self.PAIR_HIT_TITLE] = self.add_doc(self.PAIR_HIT_TITLE, "")
        self.entries[self.PAIR_MISS_TITLE] = self.add_doc(self.PAIR_MISS_TITLE, "")
        self.ids = [entry["id"] for entry in self.entries.values()]

    def entry(self, title: str) -> dict:
        return {"id": self.entries[title]["id"], "version": 1, "title": title}

    def test_each_symbol_matches_only_titles_containing_it_literally(self):
        # 裸符号查询的预期命中（默认 ID 升序）：注意“备注[甲]项”同时
        # 字面子串含 [ 与 ]，但不含 _ 或 *
        expected = {
            "_": ["进度_跟踪"],
            "*": ["重点*事项"],
            "[": ["待办[清单", self.PAIR_HIT_TITLE],
            "]": ["清单]归档", self.PAIR_HIT_TITLE],
        }
        for symbol, titles in expected.items():
            with self.subTest(symbol=symbol):
                self.assert_search(
                    symbol, [self.entry(title) for title in titles]
                )

    def test_underscore_is_not_single_character_wildcard(self):
        # 若 _ 被当作单字符通配，“进度_跟”会同时匹配下划线版与字母版；
        # 字面语义下它只命中含下划线的一篇。再以不含符号的“进度A跟”
        # 作对照：它只命中字母版，证明匹配依据是字面包含而非位置通配。
        self.assert_search("进度_跟", [self.entry("进度_跟踪")])
        self.assert_search("进度A跟", [self.entry("进度A跟踪")])
        result = run_cli("--root", str(self.kb), "search", "_")
        self.assertEqual(json.loads(result.stdout), [self.entry("进度_跟踪")])

    def test_star_is_not_glob_wildcard(self):
        # 知识库内共有 10 篇文档；若 * 按 glob 解释会命中全部，
        # 字面语义下仅“重点*事项”一篇
        result = run_cli("--root", str(self.kb), "search", "*")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        hits = json.loads(result.stdout)
        self.assertEqual(hits, [self.entry("重点*事项")])
        self.assertEqual(len(hits), 1)
        self.assert_search("重点*事", [self.entry("重点*事项")])
        # “重点A事”只命中字母版相似标题，不命中含 * 的标题：
        # * 没有替代任意字符
        self.assert_search("重点A事", [self.entry("重点A事项")])

    def test_bare_brackets_are_valid_queries_without_syntax_error(self):
        # 单独查询 [ 或 ] 合法：退出码 0、标准错误为空、无 Traceback，
        # 不解释为未闭合字符组或正则语法错误
        for symbol in ("[", "]"):
            with self.subTest(symbol=symbol):
                result = run_cli(
                    "--root", str(self.kb), "search", symbol
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
                self.assertEqual(result.stderr, b"")
                self.assertNotIn("Traceback", result.stderr.decode("utf-8"))
                self.assertIsInstance(json.loads(result.stdout), list)

    def test_bracket_shape_is_not_character_class(self):
        # [甲] 作为整体字面子串只命中“备注[甲]项”，“备注甲项”不命中；
        # 若按字符组解释，[甲] 会退化为匹配单个“甲”字，后者也会命中
        self.assert_search("[甲]", [self.entry(self.PAIR_HIT_TITLE)])
        # 没有任何标题字面子串含“[清]”（“待办[清单”在清字后是单字），
        # 字符组解释则会因含“清”字而误命中“待办[清单”
        self.assert_empty_hits("[清]")

    def test_casefold_still_applies_with_literal_underscore(self):
        # 既有 Unicode casefold 语义保留，且 _ 仍按字面参与：
        # 小写查询命中大写标题，但下划线位置不能被字母 X 顶替
        hit = self.add_doc("ABC_DEF", "")
        miss = self.add_doc("ABCXDEF", "")
        expected = [{"id": hit["id"], "version": 1, "title": "ABC_DEF"}]
        self.assert_search("abc_def", expected)
        self.assert_search("ABC_DEF", expected)
        # 字母版只被含 X 的查询命中，绝不混入下划线查询的结果
        self.assert_search(
            "abcxdef",
            [{"id": miss["id"], "version": 1, "title": "ABCXDEF"}],
        )

    def test_absent_symbol_returns_empty_array_exit_zero(self):
        # 全部最新标题都不含 #：[]、退出码 0、标准错误为空，
        # 与参数错误（退出码 2）明确区分
        for symbol in ("#", "%"):
            with self.subTest(symbol=symbol):
                self.assert_empty_hits(symbol)

    def test_missing_or_blank_query_remains_argument_error(self):
        # 合法单字符符号查询先作为对照：[、] 退出码 0
        ok = run_cli("--root", str(self.kb), "search", "[")
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stderr, b"")

        # 查询词完全缺失：退出码 2、标准输出为空、标准错误说明原因
        missing = run_cli("--root", str(self.kb), "search")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, b"")
        self.assertIn("QUERY", missing.stderr.decode("utf-8"))
        self.assertNotIn("Traceback", missing.stderr.decode("utf-8"))

        # 去除首尾空白（空格/制表符/换行）后为空：同样退出码 2
        for blank in ("", "   ", " \t\n "):
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

    def test_symbol_queries_do_not_modify_store_or_empty_bodies(self):
        all_ids = self.ids
        before_files = snapshot(self.kb)
        before_history = self.histories(all_ids)
        for doc_id in all_ids[:2]:
            show = run_cli("--root", str(self.kb), "show", str(doc_id))
            self.assertEqual(show.returncode, 0, show.stderr.decode("utf-8"))
            self.assertEqual(show.stdout, b"")

        # 有命中、无命中、字符组形态、重复分页查询与参数错误混合执行
        queries = [
            ("_", ()),
            ("*", ("--sort", "relevance")),
            ("[", ("--limit", "2")),
            ("]", ("--offset", "1")),
            ("[甲]", ()),
            ("[清]", ()),
            ("#", ()),
            ("  \t", ()),  # 参数错误，也不得改写存储
        ]
        for query, extra in queries:
            result = run_cli(
                "--root", str(self.kb), "search", query, *extra
            )
            self.assertIn(result.returncode, (0, 2))

        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.histories(all_ids), before_history)

    def test_same_symbol_query_repeated_runs_byte_identical(self):
        for query in ("[", "]", "[甲]", "[清]", "*", "#"):
            with self.subTest(query=query):
                first = run_cli("--root", str(self.kb), "search", query)
                second = run_cli("--root", str(self.kb), "search", query)
                self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
                self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8"))
                self.assertEqual(first.stdout, second.stdout)
                self.assertEqual(first.stderr, b"")
                self.assertEqual(second.stderr, b"")


if __name__ == "__main__":
    unittest.main()
