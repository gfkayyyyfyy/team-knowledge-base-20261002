"""history 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 健康知识库中，history 以退出码 0、空标准错误返回完整 JSON 数组，
  按版本号升序列出全部修订，每项仅含 version 与当时的 title；
  标题与正文相同的两次修订不被合并，旧标题不被最新标题覆盖；
- history 只读索引、不读正文：保留索引时删除旧版正文文件，或让最新版
  正文包含非法 UTF-8 字节，查询仍成功并返回同一份清单，不修复正文、
  不生成修订；
- 索引文件被替换为普通 UTF-8 文本，或索引中有含目标文档记录的
  documents 表但缺少 versions 表时，退出码 2、标准输出为空，
  标准错误含“索引无法打开或查询失败”与请求的文档 ID，不泄露 Traceback，
  不误报“文档不存在”，也不返回空数组；
- 索引健康但文档不存在时（查询不存在的正整数 ID），同样退出码 2、
  标准输出为空，但原因应为“文档不存在”，且不含索引失败提示，
  与索引读取失败明确区分；
- 成功与失败查询均不改变知识库目录条目与文件字节：不增加修订、
  不修复索引、不改写正文。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_history`` 或 ``python -m unittest discover``。
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

# 版本一/二：标题相同、正文也相同的两次独立修订
V1_TITLE = "发布流程"
V1_BODY = (
    "# 发布流程\n"
    "\n"
    "发布前先跑一遍回归测试：\n"
    "\n"
    "- 全部通过再合并\n"
    "- 保留每次修订记录\n"
).encode("utf-8")

# 版本三：标题与正文均变更
V3_TITLE = "发布检查"
V3_BODY = (
    "# 发布检查\n"
    "\n"
    "1. 回归测试全部通过\n"
    "2. 核对修订记录完整\n"
).encode("utf-8")

# 完整预期清单：硬编码字面量，不从待测调用的结果生成
EXPECTED_HISTORY = [
    {"version": 1, "title": "发布流程"},
    {"version": 2, "title": "发布流程"},
    {"version": 3, "title": "发布检查"},
]

INDEX_FAILURE = "索引无法打开或查询失败"
DOCUMENT_MISSING = "文档不存在"

MISSING_DOC_ID = 999


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


class HistoryTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_history_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, data: bytes) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_bytes(data)
        return body_file

    def add_doc(self, title: str, body: bytes) -> dict:
        """通过 add 命令新增文档，返回解析后的 {"id", "version", "title"}。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def update_doc(self, doc_id: int, title: str, body: bytes) -> dict:
        """通过 update 命令更新文档，返回解析后的 {"id", "version", "title"}。"""
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def make_three_revision_doc(self) -> int:
        """通过 add/update 准备一篇三次修订的文档。

        前两次修订标题均为“发布流程”且正文字节相同，第三次改为
        “发布检查”并更新正文。返回文档 ID。
        """
        doc = self.add_doc(V1_TITLE, V1_BODY)
        doc_id = doc["id"]
        self.assertEqual(doc["version"], 1)
        self.update_doc(doc_id, V1_TITLE, V1_BODY)
        self.update_doc(doc_id, V3_TITLE, V3_BODY)
        return doc_id

    def history(self, doc_id: int) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "history", str(doc_id))

    def body_path(self, doc_id: int, version: int) -> Path:
        return self.kb / "bodies" / str(doc_id) / f"v{version}.md"


class HealthyHistoryTests(HistoryTestCase):
    """健康对照：三次修订文档的完整历史。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_three_revision_doc()

    def test_history_returns_full_revision_list(self):
        before = snapshot(self.kb)

        result = self.history(self.doc_id)

        # 退出码 0、标准错误为空，标准输出为完整 JSON 数组
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        records = json.loads(result.stdout)
        # 与硬编码的完整预期逐条比较：版本 1/2/3、标题各自保留，
        # 不合并重复修订，也不把旧标题替换为最新标题
        self.assertEqual(records, EXPECTED_HISTORY)
        self.assertEqual([item["version"] for item in records], [1, 2, 3])
        for item in records:
            # 每项仅含版本号与当时标题两个键
            self.assertEqual(set(item), {"version", "title"})

        # 查询不改变知识库目录条目与文件字节
        self.assertEqual(snapshot(self.kb), before)

    def test_nonexistent_positive_id_is_distinct_from_index_failure(self):
        """健康索引中查询不存在的正整数 ID：原因是文档不存在而非索引失败。"""
        before = snapshot(self.kb)

        result = self.history(MISSING_DOC_ID)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err)
        self.assertIn(DOCUMENT_MISSING, err)
        self.assertIn(str(MISSING_DOC_ID), err)
        # 与索引读取失败明确区分
        self.assertNotIn(INDEX_FAILURE, err)

        self.assertEqual(snapshot(self.kb), before)


class DamagedBodyHistoryTests(HistoryTestCase):
    """索引保留但正文受损：不影响 history 返回完整清单。"""

    def test_damaged_bodies_do_not_block_history(self):
        # 每种损坏方式使用独立的知识库目录与文档
        cases = [
            ("missing_v1", 1, "missing"),
            ("missing_v2", 2, "missing"),
            ("latest_bad_utf8", 3, "badutf8"),
        ]
        for label, version, mode in cases:
            with self.subTest(mode=label):
                self.kb = self.tmp / f"kb_{label}"
                doc_id = self.make_three_revision_doc()
                target = self.body_path(doc_id, version)
                if mode == "missing":
                    target.unlink()
                else:
                    target.write_bytes(b"\xff\xfe invalid \x80")

                # 在已受损状态下查询，查询前后知识库内容必须一致
                before = snapshot(self.kb)
                result = self.history(doc_id)
                self.assertEqual(result.returncode, 0,
                                 result.stderr.decode("utf-8"))
                self.assertEqual(result.stderr, b"")
                # 完整结果与健康清单完全一致，而非仅比较条数
                self.assertEqual(json.loads(result.stdout), EXPECTED_HISTORY)
                self.assertEqual(snapshot(self.kb), before)


class BrokenIndexHistoryTests(HistoryTestCase):
    """索引无法打开或查询：统一按索引读取失败处理，而非文档不存在。"""

    def make_plain_text_index(self) -> int:
        """把索引文件替换为普通 UTF-8 文本，即不是有效的 SQLite 数据库。"""
        self.kb.mkdir(parents=True, exist_ok=True)
        (self.kb / "knowledge_base.sqlite3").write_bytes(
            "这是一段普通 UTF-8 文本，不是 SQLite 数据库\n".encode("utf-8")
        )
        return 1

    def make_documents_only_index(self) -> int:
        """创建只有 documents 表（含目标文档记录）、没有 versions 表的索引。"""
        self.kb.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        try:
            conn.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT)"
            )
            conn.execute("INSERT INTO documents (id) VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        return 1

    def test_broken_index_reports_index_failure(self):
        for name, build in [
            ("plain_text", self.make_plain_text_index),
            ("documents_only", self.make_documents_only_index),
        ]:
            with self.subTest(index=name):
                self.kb = self.tmp / f"kb_bad_index_{name}"
                doc_id = build()
                before = snapshot(self.kb)

                result = self.history(doc_id)

                self.assertEqual(result.returncode, 2,
                                 f"{name}: 退出码 {result.returncode}")
                self.assertEqual(result.stdout, b"",
                                 f"{name}: 标准输出应为空: {result.stdout!r}")
                err = result.stderr.decode("utf-8")
                self.assertNotIn("Traceback", err,
                                 f"{name}: 不应泄露堆栈\n{err}")
                self.assertIn(INDEX_FAILURE, err,
                              f"{name}: 应报索引读取失败\n{err}")
                self.assertIn(str(doc_id), err,
                              f"{name}: 应含请求的文档 ID\n{err}")
                # 不误报文档不存在，也不返回空数组
                self.assertNotIn(DOCUMENT_MISSING, err,
                                 f"{name}: 不应误报文档不存在\n{err}")
                self.assertNotIn(b"[]", result.stdout)

                # 失败查询不修复索引、不改写任何文件
                self.assertEqual(snapshot(self.kb), before)


if __name__ == "__main__":
    unittest.main()
