"""diff 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- ``diff ID --from N --to M`` 成功时退出码 0、标准错误为空，标准输出为
  UTF-8 统一差异文本，文件头为 ``--- ID/vN.md`` 与 ``+++ ID/vM.md``，
  不附加标题、绝对路径或时间戳；
- 差异按行呈现，每处变化最多前后三行上下文，邻近变化合并为同一区块；
  允许逆序（交换增删方向）与同一版本对比；
- 正文仅以 LF 划分行，CRLF 等同于 LF，输出统一使用 LF；末行是否带换行
  仍算差异，缺末尾换行的行以 ``\\ No newline at end of file`` 标示；
- 中文、空行、行内空白与 Markdown 符号按原文比较；正文相同（含仅标题
  不同）时标准输出为空，不输出文件头；
- 参数缺失或无效、文档或版本不存在、根路径不是目录、根目录不存在或
  尚无索引、任一所选正文缺失/不是普通文件/非法 UTF-8 时，退出码 2、
  标准输出为空、标准错误说明原因且不泄露 Traceback；同一版本对比仍
  校验正文可读性；
- 无论成功或失败，对比都不改变知识库目录条目与文件字节，不生成新版本。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_diff`` 或 ``python -m unittest discover``。
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

V1_BODY = "准备\n执行\n"
V2_BODY = "准备\n验证\n"


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


class DiffTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_diff_test_"))
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

    def make_two_revision_doc(
        self, v1: bytes = V1_BODY.encode("utf-8"),
        v2: bytes = V2_BODY.encode("utf-8"),
    ) -> int:
        """通过 add/update 建一个含两个修订版本的文档，返回文档 ID。"""
        doc = self.add_doc("对比文档", v1)
        self.update_doc(doc["id"], "对比文档v2", v2)
        return doc["id"]

    def diff(self, doc_id, *extra: str) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "diff", str(doc_id), *extra)

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


class HealthyDiffTests(DiffTestCase):
    """健康文档：正向、反向、同版本与仅标题变化的对比。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_forward_diff(self):
        expected = (
            "--- 1/v1.md\n"
            "+++ 1/v2.md\n"
            "@@ -1,2 +1,2 @@\n"
            " 准备\n"
            "-执行\n"
            "+验证\n"
        ).encode("utf-8")
        result = self.diff(self.doc_id, "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)

    def test_reverse_diff_swaps_direction(self):
        expected = (
            "--- 1/v2.md\n"
            "+++ 1/v1.md\n"
            "@@ -1,2 +1,2 @@\n"
            " 准备\n"
            "-验证\n"
            "+执行\n"
        ).encode("utf-8")
        result = self.diff(self.doc_id, "--from", "2", "--to", "1")
        self.assert_diff_ok(result, expected)

    def test_same_version_outputs_nothing(self):
        result = self.diff(self.doc_id, "--from", "1", "--to", "1")
        self.assert_diff_ok(result, b"")

    def test_title_only_change_outputs_nothing(self):
        doc = self.add_doc("标题一", "相同正文\n".encode("utf-8"))
        self.update_doc(doc["id"], "标题二", "相同正文\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assert_diff_ok(result, b"")

    def test_diff_does_not_create_revision_or_modify_store(self):
        before = snapshot(self.kb)
        result = self.diff(self.doc_id, "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(snapshot(self.kb), before)
        history = run_cli("--root", str(self.kb), "history", str(self.doc_id))
        self.assertEqual(len(json.loads(history.stdout)), 2)


class DiffFormatTests(DiffTestCase):
    """差异格式细节：上下文窗口、CRLF、末尾换行与原文比较。"""

    def test_context_limited_to_three_lines_and_hunks_merge(self):
        # 行2 与行9 的变化间隔 6 行（不超过 2 倍上下文）合并为一块；
        # 行18 与前一处间隔 8 行，独立成块
        v1_lines = [f"行{i}" for i in range(1, 26)]
        v2_lines = list(v1_lines)
        v2_lines[1] = "改二"
        v2_lines[8] = "改九"
        v2_lines[17] = "改十八"
        doc = self.add_doc(
            "多行", ("\n".join(v1_lines) + "\n").encode("utf-8"))
        self.update_doc(
            doc["id"], "多行", ("\n".join(v2_lines) + "\n").encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        out = result.stdout.decode("utf-8")
        hunks = [ln for ln in out.split("\n") if ln.startswith("@@")]
        self.assertEqual(hunks, ["@@ -1,12 +1,12 @@", "@@ -15,7 +15,7 @@"])
        for changed in ["-行2", "+改二", "-行9", "+改九", "-行18", "+改十八"]:
            self.assertIn(changed, out)
        # 上下文最多三行：两块之间的行13、行14 不出现在输出中
        self.assertNotIn(" 行13\n", out)
        self.assertNotIn(" 行14\n", out)
        self.assertIn(" 行12\n", out)
        self.assertIn(" 行15\n", out)

    def test_crlf_equal_to_lf(self):
        doc = self.add_doc("换行", "甲\r\n乙\r\n".encode("utf-8"))
        self.update_doc(doc["id"], "换行", "甲\n乙\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assert_diff_ok(result, b"")

    def test_crlf_change_output_uses_lf_only(self):
        doc = self.add_doc("换行", "甲\r\n乙\r\n".encode("utf-8"))
        self.update_doc(doc["id"], "换行", "甲\r\n丙\r\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertNotIn(b"\r", result.stdout)
        self.assertIn(b"-\xe4\xb9\x99\n", result.stdout)  # -乙
        self.assertIn(b"+\xe4\xb8\x99\n", result.stdout)  # +丙

    def test_trailing_newline_difference_is_marked(self):
        doc = self.add_doc("末行", "正文\n".encode("utf-8"))
        self.update_doc(doc["id"], "末行", "正文".encode("utf-8"))
        expected = (
            "--- 1/v1.md\n"
            "+++ 1/v2.md\n"
            "@@ -1 +1 @@\n"
            "-正文\n"
            "+正文\n"
            "\\ No newline at end of file\n"
        ).encode("utf-8")
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assert_diff_ok(result, expected)

    def test_raw_comparison_keeps_whitespace_and_markdown(self):
        v1 = "# 标题\n\n-  行内  空白  \n".encode("utf-8")
        v2 = "# 标题\n\n- 行内 空白\n".encode("utf-8")
        doc = self.add_doc("原文", v1)
        self.update_doc(doc["id"], "原文", v2)
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        out = result.stdout.decode("utf-8")
        # 上下文行与增删行均按原文呈现，不做格式化
        self.assertIn(" # 标题\n", out)
        self.assertIn("--  行内  空白  \n", out)
        self.assertIn("+- 行内 空白\n", out)


class DiffArgumentTests(DiffTestCase):
    """参数缺失或无效：退出码 2、标准输出为空。"""

    def setUp(self):
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_missing_from_or_to(self):
        for extra in [[], ["--from", "1"], ["--to", "2"]]:
            with self.subTest(extra=extra):
                result = self.diff(self.doc_id, *extra)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")

    def test_invalid_version_numbers(self):
        for bad in ["0", "-1", "abc", "1.5"]:
            with self.subTest(bad=bad):
                result = self.diff(self.doc_id, "--from", bad, "--to", "2")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")


class DiffErrorTests(DiffTestCase):
    """文档/版本缺失、根路径异常与正文受损的失败约定。"""

    def test_missing_document(self):
        self.make_two_revision_doc()
        result = self.diff(999, "--from", "1", "--to", "2")
        self.assert_diff_fails(result, "文档不存在")

    def test_missing_version_on_either_side(self):
        doc_id = self.make_two_revision_doc()
        for extra in [["--from", "9", "--to", "1"],
                      ["--from", "1", "--to", "9"]]:
            with self.subTest(extra=extra):
                result = self.diff(doc_id, *extra)
                self.assert_diff_fails(result, "版本不存在")

    def test_missing_root_or_index(self):
        result = self.diff(1, "--from", "1", "--to", "2")
        self.assert_diff_fails(result, "文档不存在")
        # 不创建目录或索引
        self.assertFalse(self.kb.exists())

    def test_root_is_not_a_directory(self):
        self.kb = self.tmp / "not_a_dir"
        self.kb.write_bytes(b"plain file")
        result = self.diff(1, "--from", "1", "--to", "2")
        self.assert_diff_fails(result, "不是目录")

    def test_corrupted_body_on_either_side(self):
        for mode in ["missing", "dir", "badutf8"]:
            for side in ["from", "to"]:
                with self.subTest(mode=mode, side=side):
                    self.kb = self.tmp / f"kb_{mode}_{side}"
                    doc_id = self.make_two_revision_doc()
                    body = (self.kb / "bodies" / str(doc_id)
                            / f"v{1 if side == 'from' else 2}.md")
                    if mode == "missing":
                        body.unlink()
                        reason = "缺失或不是普通文件"
                    elif mode == "dir":
                        body.unlink()
                        body.mkdir()
                        reason = "缺失或不是普通文件"
                    else:
                        body.write_bytes(b"\xff\xfe invalid \x80")
                        reason = "UTF-8"
                    before = snapshot(self.kb)
                    result = self.diff(doc_id, "--from", "1", "--to", "2")
                    self.assert_diff_fails(result, reason)
                    # 失败对比不改变知识库内容
                    self.assertEqual(snapshot(self.kb), before)

    def test_same_version_still_checks_body(self):
        self.kb = self.tmp / "kb_same_version"
        doc_id = self.make_two_revision_doc()
        (self.kb / "bodies" / str(doc_id) / "v1.md").unlink()
        result = self.diff(doc_id, "--from", "1", "--to", "1")
        self.assert_diff_fails(result, "缺失或不是普通文件")


if __name__ == "__main__":
    unittest.main()
