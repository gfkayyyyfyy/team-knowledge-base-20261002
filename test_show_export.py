"""show --output 单篇正文导出的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- ``show ID [--version N] --output FILE`` 把所选版本正文逐字节写入 FILE；
  成功时退出码 0，标准输出与标准错误均为空；省略 --version 导出最新版本；
- 相对路径按调用时工作目录解释，接受绝对路径与含空格路径，不强制扩展名；
  中文、Markdown 符号、空行、LF/CRLF、末尾换行与 UTF-8 BOM 原样保留，
  空正文生成零字节文件；
- 目标已存在（文件或目录，含指向知识库已有正文）一律拒绝，退出码 2，
  已有目标内容不变，知识库正文、索引与历史不变；
- 目标父目录不存在或不是目录、--output 缺值或为空串、文档或版本不存在、
  根目录不存在或不是目录、索引缺失或无法查询、源正文受损时，退出码 2、
  标准输出为空、标准错误说明原因且不泄露 Traceback，不产生新文件；
- 省略 --output 时 show 的标准输出行为不变；导出不初始化知识库。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_show_export`` 或 ``python -m unittest discover``。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # knowledge_base 包所在目录

V1_BODY = "准备\n执行\n".encode("utf-8")
V2_BODY = "准备\n验证\n".encode("utf-8")


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """以公开入口运行命令，捕获退出码与标准输出/错误，不检查退出码。

    cwd 用于让 --output 的相对路径按指定工作目录解释；通过 PYTHONPATH
    保证任意工作目录下都能导入 knowledge_base 包。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "knowledge_base", *args],
        capture_output=True,
        cwd=cwd if cwd is not None else ROOT,
        env=env,
    )


def snapshot(kb_dir: Path) -> list:
    """递归快照知识库目录内全部条目与文件内容，用于只读性断言。"""
    return sorted(
        (str(p.relative_to(kb_dir)), p.read_bytes() if p.is_file() else b"<dir>")
        for p in kb_dir.rglob("*")
    )


class ExportTestCase(unittest.TestCase):
    """基类：独立临时目录，内含知识库目录与导出工作目录。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_export_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self.work = self.tmp / "work dir"  # 含空格的工作目录
        self.work.mkdir()

    def _write_body(self, data: bytes, name: str) -> Path:
        body_file = self.tmp / name
        body_file.write_bytes(data)
        return body_file

    def make_two_revision_doc(self) -> int:
        """通过 add/update 建文档 1：版本一“准备\\n执行\\n”，版本二“准备\\n验证\\n”。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", "操作指南", "--file", str(self._write_body(V1_BODY, "b1.md")),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        doc_id = json.loads(result.stdout)["id"]
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", "操作指南v2", "--file", str(self._write_body(V2_BODY, "b2.md")),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        return doc_id

    def export(self, doc_id: int, *extra: str,
               cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "show", str(doc_id), *extra, cwd=cwd)

    def assert_export_fails(
        self, result: subprocess.CompletedProcess, reason: str, label: str,
    ) -> None:
        """断言导出失败：退出码 2、标准输出为空、标准错误含原因且无堆栈。"""
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(reason, err, f"{label}: 应说明原因 {reason}\n{err}")


class HealthyExportTests(ExportTestCase):
    """健康文档：默认导出版本二，--version 1 导出版本一，字节完全一致。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def assert_export_ok(self, target: Path, expected: bytes,
                         *extra: str, cwd: Path | None = None) -> None:
        result = self.export(self.doc_id, *extra, "--output", str(target), cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(target.read_bytes(), expected)

    def test_export_explicit_first_version(self):
        # 用户验收场景：show 1 --version 1 --output old.md（相对路径按 cwd 解释）
        result = run_cli(
            "--root", str(self.kb), "show", str(self.doc_id),
            "--version", "1", "--output", "old.md", cwd=self.work,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual((self.work / "old.md").read_bytes(), V1_BODY)

        # 原样重复该命令：拒绝覆盖，退出码 2，目标与历史均不变
        before = snapshot(self.kb)
        result = run_cli(
            "--root", str(self.kb), "show", str(self.doc_id),
            "--version", "1", "--output", "old.md", cwd=self.work,
        )
        self.assert_export_fails(result, "已存在", "重复导出")
        self.assertEqual((self.work / "old.md").read_bytes(), V1_BODY)
        self.assertEqual(snapshot(self.kb), before)
        history = run_cli("--root", str(self.kb), "history", str(self.doc_id))
        self.assertEqual(len(json.loads(history.stdout)), 2)

    def test_export_latest_by_default(self):
        self.assert_export_ok(self.work / "latest.md", V2_BODY)

    def test_export_absolute_path_with_spaces_no_extension(self):
        target = self.work / "导出 笔记"
        self.assert_export_ok(target, V1_BODY, "--version", "1")

    def test_export_does_not_modify_kb_or_add_version(self):
        before = snapshot(self.kb)
        self.assert_export_ok(self.work / "out.md", V2_BODY)
        self.assertEqual(snapshot(self.kb), before)

    def test_plain_show_stdout_unchanged(self):
        result = self.export(self.doc_id)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, V2_BODY)
        self.assertEqual(result.stderr, b"")


class VerbatimContentTests(ExportTestCase):
    """正文内容逐字节保留：空、CRLF、BOM、无末尾换行、Markdown 符号。"""

    CASES = {
        "empty": b"",
        "crlf": "第一行\r\n第二行\r\n".encode("utf-8"),
        "bom": b"\xef\xbb\xbf" + "# 标题\n".encode("utf-8"),
        "no_trailing_nl": "# 标题\n\n- 列表项\n\n结束".encode("utf-8"),
    }

    def test_export_preserves_bytes(self):
        for name, content in self.CASES.items():
            with self.subTest(name=name):
                self.kb = self.tmp / f"kb_{name}"
                result = run_cli(
                    "--root", str(self.kb), "add",
                    "--title", "正文", "--file",
                    str(self._write_body(content, f"body_{name}.md")),
                )
                self.assertEqual(result.returncode, 0,
                                 result.stderr.decode("utf-8"))
                doc_id = json.loads(result.stdout)["id"]
                target = self.work / f"out_{name}.md"
                result = self.export(doc_id, "--output", str(target))
                self.assertEqual(result.returncode, 0,
                                 result.stderr.decode("utf-8"))
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertEqual(target.read_bytes(), content)
                if not content:
                    self.assertEqual(target.stat().st_size, 0)


class TargetRejectionTests(ExportTestCase):
    """目标已存在或父目录不可用：一律拒绝且不改动已有内容。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_existing_file_rejected_even_with_same_content(self):
        target = self.work / "same.md"
        target.write_bytes(V2_BODY)  # 内容与所选正文相同也拒绝
        result = self.export(self.doc_id, "--output", str(target))
        self.assert_export_fails(result, "已存在", "同内容文件")
        self.assertEqual(target.read_bytes(), V2_BODY)

    def test_existing_directory_rejected(self):
        target = self.work / "a_dir"
        target.mkdir()
        result = self.export(self.doc_id, "--output", str(target))
        self.assert_export_fails(result, "已存在", "目标是目录")
        self.assertTrue(target.is_dir())

    def test_target_pointing_at_kb_body_rejected(self):
        body = self.kb / "bodies" / str(self.doc_id) / "v1.md"
        self.assertTrue(body.is_file())
        result = self.export(self.doc_id, "--output", str(body))
        self.assert_export_fails(result, "已存在", "目标是库内正文")
        self.assertEqual(body.read_bytes(), V1_BODY)

    def test_missing_parent_directory(self):
        target = self.work / "no_such_dir" / "out.md"
        before = snapshot(self.kb)
        result = self.export(self.doc_id, "--output", str(target))
        self.assert_export_fails(result, "父目录", "父目录不存在")
        self.assertFalse(target.exists())
        self.assertFalse((self.work / "no_such_dir").exists())  # 不替用户建目录
        self.assertEqual(snapshot(self.kb), before)

    def test_parent_is_not_directory(self):
        blocker = self.work / "blocker"
        blocker.write_bytes(b"x")
        result = self.export(self.doc_id, "--output", str(blocker / "out.md"))
        self.assert_export_fails(result, "父目录", "父路径不是目录")
        self.assertEqual(blocker.read_bytes(), b"x")


class ArgumentAndSourceErrorTests(ExportTestCase):
    """参数、根目录、索引与源正文异常：退出码 2，不产生新文件。"""

    def test_output_missing_value(self):
        result = run_cli("--root", str(self.kb), "show", "1", "--output")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn("Traceback", result.stderr.decode("utf-8"))

    def test_output_empty_string(self):
        result = run_cli("--root", str(self.kb), "show", "1", "--output", "")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn("Traceback", result.stderr.decode("utf-8"))

    def test_missing_document_id_argument(self):
        result = run_cli("--root", str(self.kb), "show")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_invalid_id_and_version_arguments(self):
        for extra in [["abc"], ["0"], ["1", "--version", "x"],
                      ["1", "--version", "0"], ["1", "--version", "-1"]]:
            with self.subTest(extra=extra):
                result = run_cli("--root", str(self.kb), "show", *extra,
                                 "--output", str(self.work / "out.md"))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn("Traceback", result.stderr.decode("utf-8"))

    def test_missing_document_or_version(self):
        self.doc_id = self.make_two_revision_doc()
        for extra, reason in [
            (["999"], "文档不存在"),
            ([str(self.doc_id), "--version", "99"], "版本不存在"),
        ]:
            with self.subTest(extra=extra):
                target = self.work / "should_not_exist.md"
                result = run_cli("--root", str(self.kb), "show", *extra,
                                 "--output", str(target))
                self.assert_export_fails(result, reason, "文档或版本缺失")
                self.assertFalse(target.exists())

    def test_missing_root_and_root_not_directory(self):
        target = self.work / "out.md"
        missing = self.tmp / "no_such_root"
        result = run_cli("--root", str(missing), "show", "1",
                         "--output", str(target))
        self.assert_export_fails(result, "根目录", "根目录不存在")
        self.assertFalse(missing.exists())  # 导出不初始化知识库
        self.assertFalse(target.exists())

        not_dir = self._write_body(b"x", "not_a_dir")
        result = run_cli("--root", str(not_dir), "show", "1",
                         "--output", str(target))
        self.assert_export_fails(result, "根目录", "根路径不是目录")
        self.assertFalse(target.exists())

    def test_missing_or_broken_index(self):
        # 根目录存在但没有索引文件
        self.kb.mkdir()
        target = self.work / "out.md"
        result = run_cli("--root", str(self.kb), "show", "1",
                         "--output", str(target))
        self.assert_export_fails(result, "索引", "索引缺失")
        self.assertFalse(target.exists())

        # 索引不是有效 SQLite 数据库
        (self.kb / "knowledge_base.sqlite3").write_bytes(
            "这不是 SQLite 数据库\n".encode("utf-8"))
        result = run_cli("--root", str(self.kb), "show", "1",
                         "--output", str(target))
        self.assert_export_fails(result, "索引无法打开或查询失败", "索引损坏")
        self.assertFalse(target.exists())

        # 索引缺少 versions 表
        self.kb = self.tmp / "kb_no_versions"
        self.kb.mkdir()
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        try:
            conn.execute(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT)")
            conn.execute("INSERT INTO documents (id) VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        result = run_cli("--root", str(self.kb), "show", "1",
                         "--output", str(target))
        self.assert_export_fails(result, "索引无法打开或查询失败", "缺表")
        self.assertFalse(target.exists())

    def test_corrupted_source_body(self):
        self.doc_id = self.make_two_revision_doc()
        body = self.kb / "bodies" / str(self.doc_id) / "v1.md"
        for mode in ["missing", "dir", "badutf8"]:
            with self.subTest(mode=mode):
                saved = body.read_bytes()
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
                target = self.work / f"corrupt_{mode}.md"
                result = self.export(self.doc_id, "--version", "1",
                                     "--output", str(target))
                self.assert_export_fails(result, reason, f"源正文 {mode}")
                self.assertFalse(target.exists())
                # 最新版本仍可导出，不回退不代表跳过受损版本
                ok_target = self.work / f"latest_{mode}.md"
                result = self.export(self.doc_id, "--output", str(ok_target))
                self.assertEqual(result.returncode, 0,
                                 result.stderr.decode("utf-8"))
                self.assertEqual(ok_target.read_bytes(), V2_BODY)
                # 恢复正文，供下一个子用例重新损坏
                if body.is_dir():
                    body.rmdir()
                body.write_bytes(saved)


if __name__ == "__main__":
    unittest.main()
