"""diff 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- ``diff ID --from N --to M`` 输出统一差异文本，文件头为
  ``--- ID/vN.md`` / ``+++ ID/vM.md``，不含标题、绝对路径或时间戳；
  上下文三行，邻近变化合并；允许逆序与同一版本；
- 正文仅以 LF 划分行，CRLF 视同 LF，输出统一使用 LF；
  末行是否带换行算差异，无末尾换行的行以 ``\\ No newline at end of file``
  标示；中文、空行、行内空白与 Markdown 符号按原文比较；
- 正文相同（含标题单独变化）时标准输出为空，不输出文件头；
- 参数缺失或无效、文档或版本不存在、根路径缺失或不是目录、
  索引无法查询、所选正文缺失/非普通文件/非 UTF-8 时退出码 2，
  标准输出为空，不输出半份差异，不回退到其他版本；
  同一版本对比仍校验正文可读性；
- 无论成功失败都不改变已有正文、索引与历史，不生成新版本；
- add / update / show / history / search 行为保持兼容。

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

    def diff(self, doc_id: int, *extra: str,
             root: Path | None = None) -> subprocess.CompletedProcess:
        return run_cli(
            "--root", str(root if root is not None else self.kb),
            "diff", str(doc_id), *extra,
        )

    def assert_diff_ok(
        self, doc_id: int, n: int, m: int, expected: bytes
    ) -> None:
        """断言对比成功：退出码 0、标准错误为空、输出与预期字节一致。"""
        result = self.diff(doc_id, "--from", str(n), "--to", str(m))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)

    def assert_diff_fails(
        self, result: subprocess.CompletedProcess, reason: str, label: str
    ) -> None:
        """断言对比失败：退出码 2、标准输出为空、标准错误含原因、无堆栈。"""
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(reason, err, f"{label}: 应说明原因 {reason}\n{err}")


class BasicDiffTests(DiffTestCase):
    """基本差异：上下文保留、方向交换、文件头格式。"""

    def test_forward_and_reverse(self):
        doc = self.add_doc("流程", "准备\n执行\n".encode("utf-8"))
        self.update_doc(doc["id"], "流程", "准备\n验证\n".encode("utf-8"))
        doc_id = doc["id"]

        forward = (
            f"--- {doc_id}/v1.md\n"
            f"+++ {doc_id}/v2.md\n"
            "@@ -1,2 +1,2 @@\n"
            " 准备\n"
            "-执行\n"
            "+验证\n"
        ).encode("utf-8")
        self.assert_diff_ok(doc_id, 1, 2, forward)

        reverse = (
            f"--- {doc_id}/v2.md\n"
            f"+++ {doc_id}/v1.md\n"
            "@@ -1,2 +1,2 @@\n"
            " 准备\n"
            "-验证\n"
            "+执行\n"
        ).encode("utf-8")
        self.assert_diff_ok(doc_id, 2, 1, reverse)

    def test_header_has_no_title_path_or_timestamp(self):
        doc = self.add_doc("机密标题", "a\nb\n".encode("utf-8"))
        self.update_doc(doc["id"], "机密标题", "a\nc\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        text = result.stdout.decode("utf-8")
        lines = text.split("\n")
        self.assertEqual(lines[0], f"--- {doc['id']}/v1.md")
        self.assertEqual(lines[1], f"+++ {doc['id']}/v2.md")
        self.assertNotIn("机密标题", text)
        self.assertNotIn(str(self.kb), text)
        self.assertNotIn("bodies", text)

    def test_context_limited_to_three_lines_and_hunks_merged(self):
        # 两处变化间隔 6 行上下文（> 2*3）时应分成两个块；
        # 间隔 4 行（<= 2*3）时合并为一个块
        old_lines = [f"行{i}" for i in range(1, 21)]
        new_lines = list(old_lines)
        new_lines[0] = "改1"
        new_lines[5] = "改6"
        old_body = ("\n".join(old_lines) + "\n").encode("utf-8")
        new_body = ("\n".join(new_lines) + "\n").encode("utf-8")
        doc = self.add_doc("长文", old_body)
        self.update_doc(doc["id"], "长文", new_body)

        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        text = result.stdout.decode("utf-8")
        # 间隔 4 行 <= 6 行上下文，合并为一个块
        self.assertEqual(text.count("@@"), 2)  # 一个块头含两个 @@
        self.assertIn("-行1\n", text)
        self.assertIn("+改1\n", text)
        self.assertIn("-行6\n", text)
        self.assertIn("+改6\n", text)
        # 合并后两处变化之间的行都是上下文
        self.assertIn(" 行3\n", text)
        # 上下文最多三行：最后一处变化之后只保留行7/8/9
        self.assertIn(" 行9\n", text)
        self.assertNotIn(" 行10\n", text)

    def test_far_changes_split_into_two_hunks(self):
        old_lines = [f"行{i}" for i in range(1, 21)]
        new_lines = list(old_lines)
        new_lines[0] = "改1"
        new_lines[19] = "改20"
        old_body = ("\n".join(old_lines) + "\n").encode("utf-8")
        new_body = ("\n".join(new_lines) + "\n").encode("utf-8")
        doc = self.add_doc("长文", old_body)
        self.update_doc(doc["id"], "长文", new_body)

        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        text = result.stdout.decode("utf-8")
        self.assertEqual(text.count("@@"), 4)  # 两个块头
        # 中间未变化的行不进入输出
        self.assertNotIn("行10", text)

    def test_markdown_symbols_and_blank_lines_verbatim(self):
        v1 = "# 标题\n\n- 列表  项\n\n> 引用\n"
        v2 = "# 标题\n\n- 列表 项\n\n> 引用\n"
        doc = self.add_doc("格式", v1.encode("utf-8"))
        self.update_doc(doc["id"], "格式", v2.encode("utf-8"))
        expected = (
            f"--- {doc['id']}/v1.md\n"
            f"+++ {doc['id']}/v2.md\n"
            "@@ -1,5 +1,5 @@\n"
            " # 标题\n"
            " \n"
            "-- 列表  项\n"
            "+- 列表 项\n"
            " \n"
            " > 引用\n"
        ).encode("utf-8")
        self.assert_diff_ok(doc["id"], 1, 2, expected)


class LineEndingTests(DiffTestCase):
    """换行约定：CRLF 视同 LF，输出统一 LF，末行换行算差异。"""

    def test_crlf_equal_to_lf(self):
        doc = self.add_doc("换行", "第一行\r\n第二行\r\n".encode("utf-8"))
        self.update_doc(doc["id"], "换行", "第一行\n第二行\n".encode("utf-8"))
        self.assert_diff_ok(doc["id"], 1, 2, b"")

    def test_crlf_output_uses_lf(self):
        doc = self.add_doc("换行", "甲\r\n乙\r\n".encode("utf-8"))
        self.update_doc(doc["id"], "换行", "甲\r\n丙\r\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "2")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertNotIn(b"\r", result.stdout)
        self.assertIn("-乙\n".encode("utf-8"), result.stdout)
        self.assertIn("+丙\n".encode("utf-8"), result.stdout)

    def test_trailing_newline_counts_as_difference(self):
        doc = self.add_doc("末行", "abc\n".encode("utf-8"))
        self.update_doc(doc["id"], "末行", "abc".encode("utf-8"))
        expected = (
            f"--- {doc['id']}/v1.md\n"
            f"+++ {doc['id']}/v2.md\n"
            "@@ -1 +1 @@\n"
            "-abc\n"
            "+abc\n"
            "\\ No newline at end of file\n"
        ).encode("utf-8")
        self.assert_diff_ok(doc["id"], 1, 2, expected)

    def test_both_sides_missing_trailing_newline(self):
        doc = self.add_doc("末行", "abc".encode("utf-8"))
        self.update_doc(doc["id"], "末行", "abd".encode("utf-8"))
        expected = (
            f"--- {doc['id']}/v1.md\n"
            f"+++ {doc['id']}/v2.md\n"
            "@@ -1 +1 @@\n"
            "-abc\n"
            "\\ No newline at end of file\n"
            "+abd\n"
            "\\ No newline at end of file\n"
        ).encode("utf-8")
        self.assert_diff_ok(doc["id"], 1, 2, expected)


class IdenticalBodyTests(DiffTestCase):
    """正文相同：标准输出为空，不输出文件头。"""

    def test_same_version_empty_output(self):
        doc = self.add_doc("同版", "内容\n".encode("utf-8"))
        self.assert_diff_ok(doc["id"], 1, 1, b"")

    def test_title_only_change_empty_output(self):
        doc = self.add_doc("旧标题", "相同正文\n".encode("utf-8"))
        self.update_doc(doc["id"], "新标题", "相同正文\n".encode("utf-8"))
        self.assert_diff_ok(doc["id"], 1, 2, b"")

    def test_both_empty_bodies(self):
        doc = self.add_doc("空", b"")
        self.update_doc(doc["id"], "空", b"")
        self.assert_diff_ok(doc["id"], 1, 2, b"")


class ArgumentErrorTests(DiffTestCase):
    """参数缺失或无效：退出码 2，标准输出为空。"""

    def setUp(self):
        super().setUp()
        doc = self.add_doc("参数", "正文\n".encode("utf-8"))
        self.doc_id = doc["id"]

    def test_missing_from(self):
        result = self.diff(self.doc_id, "--to", "1")
        self.assert_diff_fails(result, "--from", "缺 --from")

    def test_missing_to(self):
        result = self.diff(self.doc_id, "--from", "1")
        self.assert_diff_fails(result, "--to", "缺 --to")

    def test_missing_id(self):
        result = run_cli("--root", str(self.kb), "diff")
        self.assert_diff_fails(result, "diff", "缺 ID")

    def test_non_integer_version(self):
        for bad in ["abc", "1.5", "-1", "0"]:
            with self.subTest(bad=bad):
                result = self.diff(
                    self.doc_id, "--from", bad, "--to", "1")
                self.assert_diff_fails(result, bad, f"非法版本 {bad}")

    def test_missing_root_arg(self):
        result = run_cli("diff", "1", "--from", "1", "--to", "1")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")


class MissingDataTests(DiffTestCase):
    """文档/版本/根路径/索引缺失：退出码 2，不创建目录或索引。"""

    def test_missing_document(self):
        self.add_doc("存在", "正文\n".encode("utf-8"))
        result = self.diff(999, "--from", "1", "--to", "1")
        self.assert_diff_fails(result, "文档不存在", "文档不存在")

    def test_missing_version_either_side(self):
        doc = self.add_doc("存在", "一\n".encode("utf-8"))
        self.update_doc(doc["id"], "存在", "二\n".encode("utf-8"))
        result = self.diff(doc["id"], "--from", "1", "--to", "99")
        self.assert_diff_fails(result, "版本不存在", "新侧版本不存在")
        result = self.diff(doc["id"], "--from", "99", "--to", "1")
        self.assert_diff_fails(result, "版本不存在", "旧侧版本不存在")

    def test_missing_root_creates_nothing(self):
        missing = self.tmp / "不存在"
        result = self.diff(1, "--from", "1", "--to", "1", root=missing)
        self.assert_diff_fails(result, "文档不存在", "根目录不存在")
        self.assertFalse(missing.exists(), "失败对比不得创建根目录")

    def test_root_without_index_creates_nothing(self):
        empty = self.tmp / "空目录"
        empty.mkdir()
        result = self.diff(1, "--from", "1", "--to", "1", root=empty)
        self.assert_diff_fails(result, "文档不存在", "尚无索引")
        self.assertEqual(list(empty.iterdir()), [], "失败对比不得创建索引")

    def test_root_not_a_directory(self):
        not_dir = self.tmp / "文件"
        not_dir.write_bytes(b"not a dir")
        result = self.diff(1, "--from", "1", "--to", "1", root=not_dir)
        self.assert_diff_fails(result, "文档不存在", "根路径不是目录")

    def test_unqueryable_index(self):
        doc = self.add_doc("存在", "一\n".encode("utf-8"))
        # 用非 SQLite 内容覆盖索引文件，使查询失败
        (self.kb / "knowledge_base.sqlite3").write_bytes(b"not sqlite")
        result = self.diff(doc["id"], "--from", "1", "--to", "1")
        self.assert_diff_fails(result, "索引", "索引无法查询")


class CorruptedBodyTests(DiffTestCase):
    """所选正文缺失/非普通文件/非 UTF-8：退出码 2，不输出半份差异。"""

    def corrupt_body(self, doc_id: int, version: int, mode: str) -> str:
        body = self.kb / "bodies" / str(doc_id) / f"v{version}.md"
        if mode == "missing":
            body.unlink()
            return "缺失或不是普通文件"
        if mode == "dir":
            body.unlink()
            body.mkdir()
            return "缺失或不是普通文件"
        body.write_bytes(b"\xff\xfe invalid \x80")
        return "UTF-8"

    def test_corrupted_selected_body(self):
        for mode in ["missing", "dir", "badutf8"]:
            with self.subTest(mode=mode):
                self.kb = self.tmp / f"kb_{mode}"
                doc = self.add_doc("受损", "一\n".encode("utf-8"))
                self.update_doc(doc["id"], "受损", "二\n".encode("utf-8"))
                reason = self.corrupt_body(doc["id"], 2, mode)
                before = snapshot(self.kb)

                # 新侧受损
                result = self.diff(doc["id"], "--from", "1", "--to", "2")
                self.assert_diff_fails(result, reason, f"新侧 {mode}")
                # 旧侧受损（交换方向）
                result = self.diff(doc["id"], "--from", "2", "--to", "1")
                self.assert_diff_fails(result, reason, f"旧侧 {mode}")
                # 同一受损版本对比仍需校验，不得跳过
                result = self.diff(doc["id"], "--from", "2", "--to", "2")
                self.assert_diff_fails(result, reason, f"同版 {mode}")

                # 不输出半份差异，知识库内容不变
                self.assertEqual(snapshot(self.kb), before)
                # 健康版本之间仍可正常对比
                self.assert_diff_ok(doc["id"], 1, 1, b"")


class ReadOnlyTests(DiffTestCase):
    """无论成功失败，对比不改变正文、索引与历史，不生成新版本。"""

    def test_successful_diff_is_read_only(self):
        doc = self.add_doc("只读", "一\n".encode("utf-8"))
        self.update_doc(doc["id"], "只读", "二\n".encode("utf-8"))
        before = snapshot(self.kb)
        self.assertNotEqual(
            self.diff(doc["id"], "--from", "1", "--to", "2").stdout, b"")
        self.assertEqual(snapshot(self.kb), before)
        result = run_cli("--root", str(self.kb), "history", str(doc["id"]))
        self.assertEqual(len(json.loads(result.stdout)), 2)

    def test_failed_diff_is_read_only(self):
        doc = self.add_doc("只读", "一\n".encode("utf-8"))
        before = snapshot(self.kb)
        result = self.diff(doc["id"], "--from", "1", "--to", "9")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(snapshot(self.kb), before)


class CompatibilityTests(DiffTestCase):
    """已有子命令行为保持兼容。"""

    def test_existing_commands_unchanged(self):
        doc = self.add_doc("兼容", "原始\r\n字节".encode("utf-8"))
        self.update_doc(doc["id"], "兼容v2", "新正文\n".encode("utf-8"))
        doc_id = doc["id"]

        # show 保留原始字节与换行
        result = run_cli("--root", str(self.kb), "show", str(doc_id),
                         "--version", "1")
        self.assertEqual(result.stdout, "原始\r\n字节".encode("utf-8"))

        # history 返回全部修订
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(
            json.loads(result.stdout),
            [{"version": 1, "title": "兼容"},
             {"version": 2, "title": "兼容v2"}],
        )

        # search 仍可用
        result = run_cli("--root", str(self.kb), "search", "兼容")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(len(json.loads(result.stdout)), 1)

        # 内容相同的更新仍产生修订
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", "兼容v2", "--file",
            str(self._write_body("新正文\n".encode("utf-8"))),
        )
        self.assertEqual(json.loads(result.stdout)["version"], 3)


if __name__ == "__main__":
    unittest.main()
