"""diff 子命令对空正文参与版本对比的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定零字节正文参与对比时的公开行为：

- 版本一为空正文、版本二为 "准备\\n执行\\n" 时，正向比较的文件头依次为
  该文档的 ``v1.md`` 与 ``v2.md``，区块头为 ``@@ -0,0 +1,2 @@``，随后
  只有 ``+准备`` 与 ``+执行`` 两行；反向比较交换文件头，区块头为
  ``@@ -1,2 +0,0 @@``，两行改为删除；输出逐字节核对，每行以 LF 结束，
  标准错误为空，退出码为 0；
- 非空侧仅含 "准备" 且没有末尾换行时，无论增添还是删除，该内容行后均
  紧跟一行 ``\\ No newline at end of file``，空正文侧不多出缺换行标记；
- 非空侧使用 CRLF 时，比较结果与对应 LF 文本逐字节一致；
- 两次修订均为空正文时，即使标题不同，比较结果为零字节输出；空正文与
  自身对比同样如此，二者退出码均为 0 且标准错误为空；
- 只有一个 LF 的正文是一行空行，与零字节正文对比应出现新增或删除的
  空行，不能当作相同正文；
- 删除原本为空的所选版本正文文件后，再与有效版本对比应以退出码 2
  结束，标准输出为空，标准错误指出正文文件缺失且不含 Traceback，
  不能当作空正文或回退到其他版本；
- 无论成功或失败，对比前后知识库目录条目、已有文件字节与历史列表
  均不变。

测试数据均通过公开的 add 与 update 命令建立，每个用例使用独立的新
知识库，文档 ID 采用 add 返回的新增结果。用例可离线重复运行。
执行方式：``python -m unittest test_diff_empty_body`` 或
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

EMPTY_BODY = b""
TWO_LINE_BODY = "准备\n执行\n".encode("utf-8")
NO_NEWLINE_BODY = "准备".encode("utf-8")
CRLF_BODY = "准备\r\n执行\r\n".encode("utf-8")
BLANK_LINE_BODY = b"\n"


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
    """基类：每个用例独立临时目录与独立知识库，结束后清理自身数据。"""

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

    def make_doc(self, v1: bytes, v2: bytes) -> int:
        """通过 add/update 建一个含两个修订版本的文档，返回新增文档 ID。"""
        doc = self.add_doc("空正文对比", v1)
        self.update_doc(doc["id"], "空正文对比v2", v2)
        return doc["id"]

    def diff(self, doc_id, *extra: str) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "diff", str(doc_id), *extra)

    def diff_preserving_store(
        self, doc_id: int, *extra: str
    ) -> subprocess.CompletedProcess:
        """运行 diff 并断言知识库目录条目、文件字节与历史列表均不变。"""
        before = snapshot(self.kb)
        history_before = run_cli(
            "--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(history_before.returncode, 0,
                         history_before.stderr.decode("utf-8"))
        result = self.diff(doc_id, *extra)
        history_after = run_cli(
            "--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(snapshot(self.kb), before, "对比不应改变知识库内容")
        self.assertEqual(history_after.returncode, 0)
        self.assertEqual(history_after.stderr, b"")
        self.assertEqual(history_after.stdout, history_before.stdout,
                         "对比不应改变历史列表")
        return result

    def assert_diff_ok(
        self, result: subprocess.CompletedProcess, expected: bytes
    ) -> None:
        """断言对比成功：退出码 0、标准错误为空、输出与预期字节完全一致。"""
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)

    def assert_diff_fails(
        self, result: subprocess.CompletedProcess, *reasons: str
    ) -> None:
        """断言对比失败：退出码 2、标准输出为空、标准错误含原因且无堆栈。"""
        self.assertEqual(result.returncode, 2,
                         f"退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"不应泄露堆栈\n{err}")
        for reason in reasons:
            self.assertIn(reason, err, f"应说明原因 {reason}\n{err}")


class EmptyVsTwoLineTests(EmptyBodyDiffTestCase):
    """零字节正文与 "准备\\n执行\\n" 之间的正向与反向比较。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_doc(EMPTY_BODY, TWO_LINE_BODY)

    def test_forward_empty_to_content(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v1.md\n"
            f"+++ {doc_id}/v2.md\n"
            "@@ -0,0 +1,2 @@\n"
            "+准备\n"
            "+执行\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)

    def test_reverse_content_to_empty(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v2.md\n"
            f"+++ {doc_id}/v1.md\n"
            "@@ -1,2 +0,0 @@\n"
            "-准备\n"
            "-执行\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "2", "--to", "1")
        self.assert_diff_ok(result, expected)


class EmptyVsNoTrailingNewlineTests(EmptyBodyDiffTestCase):
    """非空侧仅含 "准备" 且无末尾换行：仅该内容行后带缺换行标记。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_doc(EMPTY_BODY, NO_NEWLINE_BODY)

    def test_forward_marks_only_content_side(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v1.md\n"
            f"+++ {doc_id}/v2.md\n"
            "@@ -0,0 +1 @@\n"
            "+准备\n"
            "\\ No newline at end of file\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)
        # 空正文侧不多出缺换行标记：全文只有一处
        self.assertEqual(result.stdout.count(b"No newline at end of file"), 1)

    def test_reverse_marks_only_content_side(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v2.md\n"
            f"+++ {doc_id}/v1.md\n"
            "@@ -1 +0,0 @@\n"
            "-准备\n"
            "\\ No newline at end of file\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "2", "--to", "1")
        self.assert_diff_ok(result, expected)
        self.assertEqual(result.stdout.count(b"No newline at end of file"), 1)


class EmptyVsCrlfTests(EmptyBodyDiffTestCase):
    """非空侧使用 CRLF 时，比较结果与对应 LF 文本逐字节一致。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_doc(EMPTY_BODY, CRLF_BODY)

    def test_forward_crlf_matches_lf_output(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v1.md\n"
            f"+++ {doc_id}/v2.md\n"
            "@@ -0,0 +1,2 @@\n"
            "+准备\n"
            "+执行\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)

    def test_reverse_crlf_matches_lf_output(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v2.md\n"
            f"+++ {doc_id}/v1.md\n"
            "@@ -1,2 +0,0 @@\n"
            "-准备\n"
            "-执行\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "2", "--to", "1")
        self.assert_diff_ok(result, expected)


class BothRevisionsEmptyTests(EmptyBodyDiffTestCase):
    """两次修订均为空正文：即使标题不同，比较结果也是零字节输出。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_doc(EMPTY_BODY, EMPTY_BODY)

    def test_empty_vs_empty_with_different_titles(self):
        result = self.diff_preserving_store(
            self.doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, b"")

    def test_empty_vs_itself(self):
        result = self.diff_preserving_store(
            self.doc_id, "--from", "1", "--to", "1")
        self.assert_diff_ok(result, b"")


class EmptyVsBlankLineTests(EmptyBodyDiffTestCase):
    """只有一个 LF 的正文是一行空行，与零字节正文对比不为空输出。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_doc(EMPTY_BODY, BLANK_LINE_BODY)

    def test_forward_adds_blank_line(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v1.md\n"
            f"+++ {doc_id}/v2.md\n"
            "@@ -0,0 +1 @@\n"
            "+\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)

    def test_reverse_removes_blank_line(self):
        doc_id = self.doc_id
        expected = (
            f"--- {doc_id}/v2.md\n"
            f"+++ {doc_id}/v1.md\n"
            "@@ -1 +0,0 @@\n"
            "-\n"
        ).encode("utf-8")
        result = self.diff_preserving_store(doc_id, "--from", "2", "--to", "1")
        self.assert_diff_ok(result, expected)


class MissingEmptyBodyFileTests(EmptyBodyDiffTestCase):
    """删除原本为空的所选版本正文文件后，对比以退出码 2 报错。"""

    def test_missing_empty_body_file_fails(self):
        doc_id = self.make_doc(EMPTY_BODY, TWO_LINE_BODY)
        missing = self.kb / "bodies" / str(doc_id) / "v1.md"
        self.assertTrue(missing.is_file())
        self.assertEqual(missing.read_bytes(), b"")
        missing.unlink()
        for extra in [["--from", "1", "--to", "2"],
                      ["--from", "2", "--to", "1"]]:
            with self.subTest(extra=extra):
                result = self.diff_preserving_store(doc_id, *extra)
                # 指出正文文件缺失，不当作空正文，也不回退到其他版本
                self.assert_diff_fails(result, "缺失或不是普通文件",
                                       f"{doc_id}/v1.md")


if __name__ == "__main__":
    unittest.main()
