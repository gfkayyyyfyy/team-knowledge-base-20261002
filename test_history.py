"""history 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 健康知识库中 history 以退出码 0 返回完整 JSON 数组，标准错误为空，
  版本按 1、2、3 升序，每项仅含版本号与当时标题：重复修订不合并，
  旧标题不被最新标题替换；
- 索引完好时，旧版正文被删除或最新版正文含非法 UTF-8 字节，均不阻止
  history 返回同一份修订清单；
- 索引文件被替换为普通 UTF-8 文本，或索引只有 documents 表（含目标文档
  记录）而缺少 versions 表时，退出码 2、标准输出为空，标准错误包含
  “索引无法打开或查询失败”与请求的文档 ID，不泄露 Traceback，不误报
  “文档不存在”，也不返回空数组；
- 健康索引中查询不存在的正整数 ID，同样退出 2、标准输出为空，但原因
  为“文档不存在”，不含索引失败提示，以此区分两类失败；
- 成功与失败的查询均只读：不改变知识库目录条目与文件字节，不增加修订、
  不修复索引、不改写正文。

每个用例使用独立临时目录并在结束后清理，可离线重复运行，不依赖网络、
外部账号或预先准备的知识库。
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

# 三次修订的固定输入：前两次标题相同、正文相同，第三次改标题并更新正文
V1_TITLE = "发布流程"
V2_TITLE = "发布流程"
V3_TITLE = "发布检查"
V1_BODY = "# 发布流程\n\n第一步：提交评审。\n\n第二步：合并主干。\n"
V2_BODY = V1_BODY
V3_BODY = "# 发布检查\n\n- 核对变更清单\n- 确认回滚方案\n"

# 健康知识库中文档 1 的完整预期清单，逐项固定，不从待测调用生成
EXPECTED_HISTORY = [
    {"version": 1, "title": V1_TITLE},
    {"version": 2, "title": V2_TITLE},
    {"version": 3, "title": V3_TITLE},
]


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
        """通过 add/update 建一个含三次修订的文档，返回文档 ID。

        版本一、二标题均为“发布流程”且正文相同，版本三标题改为
        “发布检查”并更新正文。
        """
        doc = self.add_doc(V1_TITLE, V1_BODY.encode("utf-8"))
        doc_id = doc["id"]
        self.update_doc(doc_id, V2_TITLE, V2_BODY.encode("utf-8"))
        self.update_doc(doc_id, V3_TITLE, V3_BODY.encode("utf-8"))
        return doc_id

    def history(self, doc_id: int) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "history", str(doc_id))

    def assert_history_ok(self, doc_id: int, expected: list) -> None:
        """断言查询成功：退出码 0、标准错误为空、输出与预期清单完全一致。"""
        result = self.history(doc_id)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), expected)


class HealthyHistoryTests(HistoryTestCase):
    """健康对照：三次修订的文档返回完整、不合并、不改写的修订清单。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_three_revision_doc()

    def test_full_history_returned_verbatim(self):
        before = snapshot(self.kb)
        self.assert_history_ok(self.doc_id, EXPECTED_HISTORY)
        # 成功查询只读：不增加修订、不改写正文或索引
        self.assertEqual(snapshot(self.kb), before)

    def test_items_contain_only_version_and_title(self):
        result = self.history(self.doc_id)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        items = json.loads(result.stdout)
        for item in items:
            self.assertEqual(sorted(item.keys()), ["title", "version"])

    def test_missing_document_reports_not_found(self):
        """健康索引中查询不存在的正整数 ID：报“文档不存在”而非索引失败。"""
        before = snapshot(self.kb)
        result = self.history(999)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"不应泄露堆栈\n{err}")
        self.assertIn("文档不存在", err, f"应报文档不存在\n{err}")
        self.assertIn("999", err, f"应含请求的文档 ID\n{err}")
        self.assertNotIn("索引无法打开或查询失败", err,
                         f"不应误报索引读取失败\n{err}")
        # 失败查询同样只读
        self.assertEqual(snapshot(self.kb), before)


class CorruptedBodyHistoryTests(HistoryTestCase):
    """正文受损但索引完好：history 仍返回与健康对照完全一致的清单。"""

    def test_old_version_body_deleted(self):
        """删除旧版（v1）正文文件后，清单不受影响。"""
        doc_id = self.make_three_revision_doc()
        (self.kb / "bodies" / str(doc_id) / "v1.md").unlink()
        before = snapshot(self.kb)
        self.assert_history_ok(doc_id, EXPECTED_HISTORY)
        self.assertEqual(snapshot(self.kb), before)

    def test_latest_version_body_invalid_utf8(self):
        """最新版（v3）正文含非法 UTF-8 字节后，清单不受影响。"""
        doc_id = self.make_three_revision_doc()
        (self.kb / "bodies" / str(doc_id) / "v3.md").write_bytes(
            b"\xff\xfe invalid \x80"
        )
        before = snapshot(self.kb)
        self.assert_history_ok(doc_id, EXPECTED_HISTORY)
        self.assertEqual(snapshot(self.kb), before)


class BrokenIndexHistoryTests(HistoryTestCase):
    """索引无法打开或缺少 versions 表：统一报索引读取失败，而非文档不存在。"""

    REASON = "索引无法打开或查询失败"

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

    def assert_index_failure(
        self, result: subprocess.CompletedProcess, doc_id: int, label: str
    ) -> None:
        """断言索引读取失败：退出码 2、输出为空、错误含原因与文档 ID。

        不得泄露 Traceback，不得误报“文档不存在”，也不得返回空数组。
        """
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(self.REASON, err, f"{label}: 应报索引读取失败\n{err}")
        self.assertIn(str(doc_id), err, f"{label}: 应含文档 ID\n{err}")
        self.assertNotIn("文档不存在", err, f"{label}: 不应误报文档不存在\n{err}")

    def test_plain_text_index(self):
        self.kb = self.tmp / "kb_plain_text_index"
        doc_id = self.make_plain_text_index()
        before = snapshot(self.kb)
        self.assert_index_failure(self.history(doc_id), doc_id, "普通文本索引")
        # 失败查询不修复或重建索引，不改动任何条目
        self.assertEqual(snapshot(self.kb), before)

    def test_documents_only_index(self):
        self.kb = self.tmp / "kb_documents_only_index"
        doc_id = self.make_documents_only_index()
        before = snapshot(self.kb)
        self.assert_index_failure(self.history(doc_id), doc_id, "缺少 versions 表")
        self.assertEqual(snapshot(self.kb), before)


if __name__ == "__main__":
    unittest.main()
