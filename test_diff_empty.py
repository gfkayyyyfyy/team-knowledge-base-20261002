"""零字节正文参与 diff 版本对比的命令行回归测试。

通过公开入口 ``python -m knowledge_base``、``--root`` 与 ``diff`` 子命令
观察真实的退出码、标准输出与标准错误，不直接调用内部差异函数，固定空
正文相关的公开比较约定：

- 版本一为零字节正文、版本二为有内容正文时，空侧在区块头中以起始行 0、
  行数 0 表示（如 ``@@ -0,0 +1,2 @@``），正向只有新增行，反向交换文件头
  并全部变为删除行，输出逐字节核对、每行以 LF 结束；
- 非空侧仅一行且没有末尾换行时，该内容行之后紧跟
  ``\\ No newline at end of file``，空正文侧不多出缺换行标记；
- 非空侧使用 CRLF 时，比较结果与对应 LF 文本一致，输出不含 CR；
- 两次修订均为零字节正文（即使标题不同）或零字节正文与自身对比时，
  标准输出为零字节，退出码 0、标准错误为空；
- 只有一个 LF 的正文是一行空行，与零字节正文不同，应出现新增或删除的
  空行，不能当作相同正文；
- 删除原本为空的所选版本正文文件后再与有效版本对比，以退出码 2 结束、
  标准输出为空、标准错误指出正文文件缺失且不含 Traceback，不能当作空
  正文或回退到其他版本；
- 成功与失败用例都核对比较前后知识库目录条目、已有文件字节与历史列表
  不变。

测试数据只经 add / update 建立，每个用例使用独立临时知识库，文档 ID
取自 add 结果；可离线重复运行。
执行方式：``python -m unittest test_diff_empty`` 或
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

EMPTY = b""
TWO_LINES = "准备\n执行\n".encode("utf-8")
TWO_LINES_CRLF = "准备\r\n执行\r\n".encode("utf-8")
ONE_LINE_NO_NL = "准备".encode("utf-8")
ONE_LINE_CRLF = "准备\r\n".encode("utf-8")
SINGLE_LF = b"\n"


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


class EmptyBodyDiffTestCase(unittest.TestCase):
    """基类：每个用例独立临时知识库，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_diff_empty_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, data: bytes) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_bytes(data)
        return body_file

    def add_doc(self, title: str, body: bytes) -> dict:
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def update_doc(self, doc_id: int, title: str, body: bytes) -> dict:
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def diff(self, doc_id, from_v: int, to_v: int) -> subprocess.CompletedProcess:
        return run_cli(
            "--root", str(self.kb), "diff", str(doc_id),
            "--from", str(from_v), "--to", str(to_v),
        )

    def history_stdout(self, doc_id: int) -> bytes:
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return result.stdout

    def assert_store_unchanged(
        self, before: list, history_before: bytes, doc_id: int
    ) -> None:
        """目录条目、已有文件字节与历史列表在比较前后完全一致。"""
        self.assertEqual(snapshot(self.kb), before)
        self.assertEqual(self.history_stdout(doc_id), history_before)

    def headers(self, doc_id: int, from_v: int, to_v: int) -> bytes:
        return (
            f"--- {doc_id}/v{from_v}.md\n"
            f"+++ {doc_id}/v{to_v}.md\n"
        ).encode("utf-8")


class EmptyAgainstTwoLinesTests(EmptyBodyDiffTestCase):
    """零字节正文与两行 UTF-8 文本之间的正反向比较。"""

    def setUp(self) -> None:
        super().setUp()
        doc = self.add_doc("空正文版本一", EMPTY)
        self.doc_id = doc["id"]
        self.update_doc(self.doc_id, "有内容版本二", TWO_LINES)

    def test_empty_to_two_lines_full_output(self):
        result = self.diff(self.doc_id, 1, 2)
        expected = (
            self.headers(self.doc_id, 1, 2)
            + b"@@ -0,0 +1,2 @@\n"
            + "+准备\n".encode("utf-8")
            + "+执行\n".encode("utf-8")
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        # 逐字节核对：文件头、区块头、两行新增，且每行均以 LF 结束
        self.assertEqual(result.stdout, expected)
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertNotIn(b"\r", result.stdout)
        lines = result.stdout.decode("utf-8").split("\n")
        self.assertEqual(lines[:3], [
            f"--- {self.doc_id}/v1.md",
            f"+++ {self.doc_id}/v2.md",
            "@@ -0,0 +1,2 @@",
        ])
        # 区块头之后只有 +准备 与 +执行 两行（末尾 split 产生空串）
        self.assertEqual(lines[3:-1], ["+准备", "+执行"])
        self.assertEqual(lines[-1], "")

    def test_two_lines_to_empty_full_output(self):
        result = self.diff(self.doc_id, 2, 1)
        expected = (
            self.headers(self.doc_id, 2, 1)
            + b"@@ -1,2 +0,0 @@\n"
            + "-准备\n".encode("utf-8")
            + "-执行\n".encode("utf-8")
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        # 反向比较：交换文件头，区块头行数归零，新增全部改为删除
        self.assertEqual(result.stdout, expected)
        self.assertNotIn(b"\r", result.stdout)
        lines = result.stdout.decode("utf-8").split("\n")
        self.assertEqual(lines[:3], [
            f"--- {self.doc_id}/v2.md",
            f"+++ {self.doc_id}/v1.md",
            "@@ -1,2 +0,0 @@",
        ])
        self.assertEqual(lines[3:-1], ["-准备", "-执行"])
        self.assertEqual(lines[-1], "")

    def test_successful_diff_leaves_store_unchanged(self):
        before = snapshot(self.kb)
        history_before = self.history_stdout(self.doc_id)
        result = self.diff(self.doc_id, 1, 2)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assert_store_unchanged(before, history_before, self.doc_id)
        # 历史仍为两个版本，标题保持各修订时的原样
        history = json.loads(history_before)
        self.assertEqual(history, [
            {"version": 1, "title": "空正文版本一"},
            {"version": 2, "title": "有内容版本二"},
        ])


class EmptyAgainstNoFinalNewlineTests(EmptyBodyDiffTestCase):
    """非空侧仅一行“准备”且无末尾换行：缺换行标记只属于非空侧。"""

    def setUp(self) -> None:
        super().setUp()
        doc = self.add_doc("空正文", EMPTY)
        self.doc_id = doc["id"]
        self.update_doc(self.doc_id, "无末尾换行", ONE_LINE_NO_NL)

    def test_empty_to_line_without_newline(self):
        result = self.diff(self.doc_id, 1, 2)
        expected = (
            self.headers(self.doc_id, 1, 2)
            + b"@@ -0,0 +1 @@\n"
            + "+准备\n".encode("utf-8")
            + b"\\ No newline at end of file\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)
        lines = result.stdout.decode("utf-8").split("\n")
        # 内容行之后紧跟唯一一行缺换行标记，空正文侧没有该标记
        self.assertEqual(lines[3:-1], ["+准备", "\\ No newline at end of file"])
        self.assertEqual(
            result.stdout.count(b"\\ No newline at end of file\n"), 1
        )

    def test_line_without_newline_to_empty(self):
        result = self.diff(self.doc_id, 2, 1)
        expected = (
            self.headers(self.doc_id, 2, 1)
            + b"@@ -1 +0,0 @@\n"
            + "-准备\n".encode("utf-8")
            + b"\\ No newline at end of file\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)
        lines = result.stdout.decode("utf-8").split("\n")
        # 删除方向同样只有非空侧内容行之后出现缺换行标记
        self.assertEqual(lines[3:-1], ["-准备", "\\ No newline at end of file"])
        self.assertEqual(
            result.stdout.count(b"\\ No newline at end of file\n"), 1
        )


class EmptyAgainstCrlfTests(EmptyBodyDiffTestCase):
    """非空侧使用 CRLF 时结果与对应 LF 文本一致。"""

    def test_empty_to_crlf_two_lines_matches_lf(self):
        doc = self.add_doc("空正文", EMPTY)
        doc_id = doc["id"]
        self.update_doc(doc_id, "CRLF 两行", TWO_LINES_CRLF)
        result = self.diff(doc_id, 1, 2)
        expected = (
            self.headers(doc_id, 1, 2)
            + b"@@ -0,0 +1,2 @@\n"
            + "+准备\n".encode("utf-8")
            + "+执行\n".encode("utf-8")
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)
        self.assertNotIn(b"\r", result.stdout)

    def test_crlf_two_lines_to_empty_matches_lf(self):
        doc = self.add_doc("CRLF 两行", TWO_LINES_CRLF)
        doc_id = doc["id"]
        self.update_doc(doc_id, "空正文", EMPTY)
        result = self.diff(doc_id, 1, 2)
        expected = (
            self.headers(doc_id, 1, 2)
            + b"@@ -1,2 +0,0 @@\n"
            + "-准备\n".encode("utf-8")
            + "-执行\n".encode("utf-8")
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)
        self.assertNotIn(b"\r", result.stdout)

    def test_empty_to_crlf_single_line_has_no_missing_newline_marker(self):
        # CRLF 同样是末尾换行：归一化后为 "准备\n"，不应出现缺换行标记
        doc = self.add_doc("空正文", EMPTY)
        doc_id = doc["id"]
        self.update_doc(doc_id, "CRLF 单行", ONE_LINE_CRLF)
        result = self.diff(doc_id, 1, 2)
        expected = (
            self.headers(doc_id, 1, 2)
            + b"@@ -0,0 +1 @@\n"
            + "+准备\n".encode("utf-8")
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)
        self.assertNotIn(b"\\ No newline at end of file", result.stdout)


class BothSidesEmptyTests(EmptyBodyDiffTestCase):
    """两侧均为零字节正文：零字节输出，标题差异不产生差异。"""

    def test_two_empty_revisions_different_titles_output_zero_bytes(self):
        doc = self.add_doc("空标题甲", EMPTY)
        doc_id = doc["id"]
        self.update_doc(doc_id, "另一个完全不同的标题", EMPTY)
        before = snapshot(self.kb)
        history_before = self.history_stdout(doc_id)
        result = self.diff(doc_id, 1, 2)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"")
        self.assert_store_unchanged(before, history_before, doc_id)

    def test_empty_body_compared_with_itself_output_zero_bytes(self):
        doc = self.add_doc("空正文自身", EMPTY)
        doc_id = doc["id"]
        before = snapshot(self.kb)
        history_before = self.history_stdout(doc_id)
        result = self.diff(doc_id, 1, 1)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"")
        self.assert_store_unchanged(before, history_before, doc_id)


class SingleLfVersusEmptyTests(EmptyBodyDiffTestCase):
    """只有一个 LF 的正文是一行空行，与零字节正文不是相同正文。"""

    def setUp(self) -> None:
        super().setUp()
        doc = self.add_doc("空正文", EMPTY)
        self.doc_id = doc["id"]
        self.update_doc(self.doc_id, "一个空行", SINGLE_LF)

    def test_empty_to_single_lf_adds_blank_line(self):
        result = self.diff(self.doc_id, 1, 2)
        expected = (
            self.headers(self.doc_id, 1, 2)
            + b"@@ -0,0 +1 @@\n"
            + b"+\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        # 输出非空：出现新增的空行，而不是把两侧当作相同正文
        self.assertEqual(result.stdout, expected)

    def test_single_lf_to_empty_deletes_blank_line(self):
        result = self.diff(self.doc_id, 2, 1)
        expected = (
            self.headers(self.doc_id, 2, 1)
            + b"@@ -1 +0,0 @@\n"
            + b"-\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)


class MissingEmptyBodyFileTests(EmptyBodyDiffTestCase):
    """删除原本为空的所选版本正文文件后必须报错，不能当作空正文。"""

    def _build_with_missing_empty_side(self, side: str) -> tuple[int, list, bytes]:
        self.kb = self.tmp / f"kb_missing_{side}"
        doc = self.add_doc("空正文版本", EMPTY)
        doc_id = doc["id"]
        self.update_doc(doc_id, "有效版本", TWO_LINES)
        # 删除原本保存零字节正文的 v1.md
        missing = self.kb / "bodies" / str(doc_id) / "v1.md"
        self.assertTrue(missing.is_file())
        self.assertEqual(missing.read_bytes(), b"")
        missing.unlink()
        return doc_id, snapshot(self.kb), self.history_stdout(doc_id)

    def test_missing_empty_body_file_on_either_side_fails(self):
        for side, from_v, to_v in [("from", 1, 2), ("to", 2, 1)]:
            with self.subTest(side=side):
                doc_id, before, history_before = (
                    self._build_with_missing_empty_side(side)
                )
                result = self.diff(doc_id, from_v, to_v)
                # 退出码 2、标准输出为空，不能按零字节正文产出差异
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                err = result.stderr.decode("utf-8")
                self.assertNotIn("Traceback", err)
                # 标准错误明确指出正文文件缺失（含文档、版本与相对路径）
                self.assertIn("正文文件缺失", err)
                self.assertIn(str(doc_id), err)
                self.assertIn("版本 1", err)
                self.assertIn(f"bodies/{doc_id}/v1.md", err)
                # 失败比较不改变目录条目、已有文件字节与历史列表
                self.assert_store_unchanged(before, history_before, doc_id)
                # 历史仍记录两个版本，不回退到其他版本、不修复缺失文件
                history = json.loads(history_before)
                self.assertEqual([item["version"] for item in history], [1, 2])
                self.assertFalse(
                    (self.kb / "bodies" / str(doc_id) / "v1.md").exists()
                )


if __name__ == "__main__":
    unittest.main()
