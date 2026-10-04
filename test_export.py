"""show --output 单篇正文导出的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- ``--output FILE`` 把所选版本正文逐字节导出为本地文件：省略 ``--version``
  导出最新版本，指定时只导出该历史版本；成功时退出码 0，标准输出与
  标准错误均为空，目标文件与源正文逐字节一致；
- 相对路径按调用时工作目录解释，接受绝对路径、含空格的路径与无扩展名；
  中文、Markdown 符号、空行、CRLF、无末尾换行、UTF-8 BOM 与空正文
  （零字节文件）全部原样保留；
- 目标路径已存在（文件、目录、符号链接，哪怕内容相同）一律拒绝，
  目标指向知识库已有正文时也不例外；父目录必须已存在且为目录，
  导出不创建目录；
- 缺少 ID、ID/版本非法、``--output`` 缺值或为空字符串、根目录不存在或
  不是目录、索引缺失或无法查询、文档或版本不存在、源正文缺失/不是普通
  文件/非 UTF-8、目标无法写入时退出码 2，标准输出为空，标准错误说明
  对应原因，不输出调用栈，不回退其他版本；
- 失败不改变已有目标；写入失败不留下本次新建的文件；导出不修改源正文、
  SQLite 索引或历史记录，不生成新版本，也不初始化知识库；
- 省略 ``--output`` 时仍为原样写标准输出的既有行为。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_export`` 或 ``python -m unittest discover``。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # knowledge_base 包所在目录

# 版本一：准备\n执行\n；版本二：准备\n验证\n（\n 为 LF）
V1_BODY = "准备\n执行\n".encode("utf-8")
V2_BODY = "准备\n验证\n".encode("utf-8")


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """以公开入口运行命令，捕获退出码与标准输出/错误，不检查退出码。

    把仓库根目录加入 PYTHONPATH，使工作目录切换到临时目录时
    ``python -m knowledge_base`` 仍可导入；默认工作目录仍为仓库根目录。
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-m", "knowledge_base", *args],
        capture_output=True,
        cwd=str(cwd if cwd is not None else ROOT),
        env=env,
    )


def snapshot(kb_dir: Path) -> list:
    """递归快照知识库目录内全部条目与文件内容，用于只读性断言。"""
    return sorted(
        (str(p.relative_to(kb_dir)), p.read_bytes() if p.is_file() else b"<dir>")
        for p in kb_dir.rglob("*")
    )


class ExportTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_export_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self.work = self.tmp / "work"
        self.work.mkdir()
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

    def make_two_revision_doc(self) -> int:
        doc = self.add_doc("验收文档", V1_BODY)
        self.update_doc(doc["id"], "验收文档v2", V2_BODY)
        return doc["id"]

    def export(self, doc_id: int, target: str, *extra: str,
               cwd: Path | None = None) -> subprocess.CompletedProcess:
        return run_cli(
            "--root", str(self.kb), "show", str(doc_id), *extra,
            "--output", target, cwd=cwd,
        )

    def assert_export_ok(self, doc_id: int, target: Path, expected: bytes,
                         *extra: str, cwd: Path | None = None) -> None:
        """断言导出成功：退出码 0、两侧输出为空、文件与预期逐字节一致。"""
        result = self.export(doc_id, str(target), *extra, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), expected)

    def assert_export_fails(self, result: subprocess.CompletedProcess,
                           label: str) -> None:
        """断言导出失败：退出码 2、标准输出为空、无 Traceback。"""
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertTrue(err.strip(), f"{label}: 标准错误应说明原因")


class HealthyExportTests(ExportTestCase):
    """两版本健康文档：导出历史版本/最新版本，重复导出被拒绝。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_export_explicit_version_byte_identical(self):
        target = self.work / "old.md"
        self.assert_export_ok(self.doc_id, target, V1_BODY, "--version", "1")

    def test_export_default_latest(self):
        target = self.work / "latest.md"
        self.assert_export_ok(self.doc_id, target, V2_BODY)

    def test_relative_path_resolved_against_cwd(self):
        # 目标是相对路径，进程工作目录为 self.work，文件应落在其中
        result = self.export(self.doc_id, "old.md", "--version", "1",
                             cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual((self.work / "old.md").read_bytes(), V1_BODY)

    def test_absolute_path_with_spaces_and_no_extension(self):
        target = self.work / "sub dir" / "操作 指南"
        target.parent.mkdir()
        self.assert_export_ok(self.doc_id, target, V1_BODY, "--version", "1")

    def test_repeat_export_rejected_and_everything_unchanged(self):
        target = self.work / "old.md"
        self.assert_export_ok(self.doc_id, target, V1_BODY, "--version", "1")

        result = self.export(self.doc_id, str(target), "--version", "1")
        self.assert_export_fails(result, "重复导出")
        self.assertIn("已存在", result.stderr.decode("utf-8"))

        # 目标内容与文档历史均保持不变
        self.assertEqual(target.read_bytes(), V1_BODY)
        history = run_cli("--root", str(self.kb), "history", str(self.doc_id))
        self.assertEqual(json.loads(history.stdout),
                         [{"version": 1, "title": "验收文档"},
                          {"version": 2, "title": "验收文档v2"}])

    def test_export_creates_no_new_version_and_leaves_kb_untouched(self):
        before = snapshot(self.kb)
        result = self.export(self.doc_id, str(self.work / "x.md"),
                             "--version", "1")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(snapshot(self.kb), before)


class BytePreservationTests(ExportTestCase):
    """空正文、CRLF、无末尾换行、BOM 等逐字节保留。"""

    CASES = {
        "empty": b"",
        "crlf": "第一行\r\n第二行\r\n".encode("utf-8"),
        "no_trailing_nl": "没有末尾换行".encode("utf-8"),
        "bom": "\ufeff# 标题\n\n正文\n".encode("utf-8"),
        "markdown": "# H\n\n- a\n- b\n\n> 引用\n".encode("utf-8"),
    }

    def test_bodies_exported_verbatim(self):
        for name, content in self.CASES.items():
            with self.subTest(name=name):
                self.kb = self.tmp / f"kb_{name}"
                work = self.tmp / f"work_{name}"
                work.mkdir()
                doc = self.add_doc("正文", content)
                target = work / f"out_{name}.bin"
                self.assert_export_ok(doc["id"], target, content, cwd=work)
                if name == "empty":
                    self.assertEqual(target.stat().st_size, 0)


class ExistingTargetTests(ExportTestCase):
    """目标已存在时一律拒绝，无论类型或内容。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def assert_rejected(self, target: Path, label: str) -> None:
        before = target.read_bytes() if target.is_file() else None
        result = self.export(self.doc_id, str(target), "--version", "1")
        self.assert_export_fails(result, label)
        self.assertIn("已存在", result.stderr.decode("utf-8"))
        self.assertFalse((self.work / "unexpected").exists())
        if before is not None:
            self.assertEqual(target.read_bytes(), before)

    def test_existing_file_even_with_identical_content_rejected(self):
        target = self.work / "same.md"
        target.write_bytes(V1_BODY)
        self.assert_rejected(target, "同内容文件")

    def test_existing_directory_rejected(self):
        target = self.work / "a_dir"
        target.mkdir()
        self.assert_rejected(target, "目录")

    def test_existing_dangling_symlink_rejected(self):
        target = self.work / "link"
        target.symlink_to(self.work / "missing_target")
        self.assert_rejected(target, "悬空符号链接")

    def test_target_pointing_at_kb_body_rejected(self):
        body = self.kb / "bodies" / str(self.doc_id) / "v1.md"
        result = self.export(self.doc_id, str(body), "--version", "1")
        self.assert_export_fails(result, "知识库正文路径")
        self.assertIn("已存在", result.stderr.decode("utf-8"))
        self.assertEqual(body.read_bytes(), V1_BODY)


class TargetPathValidationTests(ExportTestCase):
    """父目录缺失/不是目录、--output 缺值或为空字符串。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_missing_parent(self):
        target = self.work / "no" / "such" / "f.md"
        result = self.export(self.doc_id, str(target), "--version", "1")
        self.assert_export_fails(result, "父目录缺失")
        self.assertIn("父目录", result.stderr.decode("utf-8"))
        self.assertFalse(target.exists())

    def test_parent_is_regular_file(self):
        blocker = self.work / "not_a_dir"
        blocker.write_bytes(b"x")
        target = blocker / "f.md"
        result = self.export(self.doc_id, str(target), "--version", "1")
        self.assert_export_fails(result, "父目录是文件")
        self.assertIn("父目录", result.stderr.decode("utf-8"))
        self.assertEqual(blocker.read_bytes(), b"x")

    def test_output_empty_string(self):
        result = run_cli(
            "--root", str(self.kb), "show", str(self.doc_id),
            "--version", "1", "--output", "",
        )
        self.assert_export_fails(result, "--output 空字符串")

    def test_output_missing_value(self):
        result = run_cli(
            "--root", str(self.kb), "show", str(self.doc_id), "--output",
        )
        self.assert_export_fails(result, "--output 缺值")


class ArgumentAndLookupFailureTests(ExportTestCase):
    """ID/版本问题与文档、版本不存在。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id = self.make_two_revision_doc()

    def test_missing_id(self):
        result = run_cli(
            "--root", str(self.kb), "show", "--output", str(self.work / "x"),
        )
        self.assert_export_fails(result, "缺少 ID")

    def test_invalid_id_or_version(self):
        for bad_id in ["0", "-1", "1.0", "abc"]:
            with self.subTest(bad_id=bad_id):
                result = run_cli(
                    "--root", str(self.kb), "show", bad_id,
                    "--output", str(self.work / "x"),
                )
                self.assert_export_fails(result, f"ID={bad_id}")
        for bad_version in ["0", "-3", "v2"]:
            with self.subTest(bad_version=bad_version):
                result = run_cli(
                    "--root", str(self.kb), "show", str(self.doc_id),
                    "--version", bad_version,
                    "--output", str(self.work / "x"),
                )
                self.assert_export_fails(result, f"version={bad_version}")

    def test_missing_document(self):
        target = self.work / "x.md"
        result = self.export(999, str(target))
        self.assert_export_fails(result, "文档不存在")
        self.assertIn("文档不存在", result.stderr.decode("utf-8"))
        self.assertFalse(target.exists())

    def test_missing_version(self):
        target = self.work / "x.md"
        result = self.export(self.doc_id, str(target), "--version", "99")
        self.assert_export_fails(result, "版本不存在")
        self.assertIn("版本不存在", result.stderr.decode("utf-8"))
        self.assertFalse(target.exists())


class RootAndIndexFailureTests(ExportTestCase):
    """根目录异常与索引缺失/损坏：退出码 2，不初始化、不产生文件。"""

    def target(self) -> Path:
        return self.tmp / "out.md"

    def test_root_missing(self):
        result = self.export(1, str(self.target()))
        self.assert_export_fails(result, "根目录不存在")
        self.assertIn("根目录不存在", result.stderr.decode("utf-8"))
        self.assertFalse(self.kb.exists())
        self.assertFalse(self.target().exists())

    def test_root_is_regular_file(self):
        self.kb.write_bytes(b"plain file")
        result = self.export(1, str(self.target()))
        self.assert_export_fails(result, "根路径不是目录")
        self.assertIn("不是目录", result.stderr.decode("utf-8"))
        self.assertFalse(self.target().exists())

    def test_index_missing(self):
        self.kb.mkdir()
        result = self.export(1, str(self.target()))
        self.assert_export_fails(result, "索引缺失")
        self.assertIn("索引", result.stderr.decode("utf-8"))
        self.assertFalse(self.target().exists())
        # 不初始化知识库
        self.assertFalse((self.kb / "knowledge_base.sqlite3").exists())

    def test_index_unqueryable(self):
        doc_id = self.make_two_revision_doc()
        (self.kb / "knowledge_base.sqlite3").write_bytes(
            "这是一段普通 UTF-8 文本，不是 SQLite 数据库\n".encode("utf-8")
        )
        result = self.export(doc_id, str(self.target()), "--version", "1")
        self.assert_export_fails(result, "索引无法查询")
        err = result.stderr.decode("utf-8")
        self.assertIn("索引无法打开或查询失败", err)
        self.assertIn(str(doc_id), err)
        self.assertFalse(self.target().exists())


class SourceBodyFailureTests(ExportTestCase):
    """源正文缺失、变成目录或含非法 UTF-8：失败且不产生目标。"""

    def corrupt(self, doc_id: int, version: int, mode: str) -> str:
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

    def test_corrupted_source(self):
        for mode in ["missing", "dir", "badutf8"]:
            with self.subTest(mode=mode):
                self.kb = self.tmp / f"kb_{mode}"
                doc_id = self.make_two_revision_doc()
                reason = self.corrupt(doc_id, 1, mode)
                before = snapshot(self.kb)
                target = self.tmp / f"out_{mode}.md"

                result = self.export(doc_id, str(target), "--version", "1")
                self.assert_export_fails(result, f"源正文 {mode}")
                self.assertIn(reason, result.stderr.decode("utf-8"))
                self.assertFalse(target.exists())
                self.assertEqual(snapshot(self.kb), before)


class WriteFailureTests(ExportTestCase):
    """目标无法写入时退出码 2，不留下本次新建的文件。"""

    @unittest.skipIf(
        os.geteuid() == 0 if hasattr(os, "geteuid") else False,
        "root 会绕过目录写权限",
    )
    def test_read_only_parent_leaves_no_file(self):
        doc_id = self.make_two_revision_doc()
        ro = self.work / "ro"
        ro.mkdir()
        ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(ro.chmod, stat.S_IRWXU)
        target = ro / "f.md"

        result = self.export(doc_id, str(target), "--version", "1")
        self.assert_export_fails(result, "只读父目录")
        self.assertIn("无法写入", result.stderr.decode("utf-8"))
        self.assertFalse(target.exists())
        self.assertEqual(list(ro.iterdir()), [])


class NoOutputUnchangedTests(ExportTestCase):
    """省略 --output：仍为原样写标准输出的既有行为。"""

    def test_stdout_behavior_unchanged(self):
        doc_id = self.make_two_revision_doc()
        latest = run_cli("--root", str(self.kb), "show", str(doc_id))
        self.assertEqual(latest.returncode, 0)
        self.assertEqual(latest.stdout, V2_BODY)
        self.assertEqual(latest.stderr, b"")
        history = run_cli(
            "--root", str(self.kb), "show", str(doc_id), "--version", "1"
        )
        self.assertEqual(history.returncode, 0)
        self.assertEqual(history.stdout, V1_BODY)
        self.assertEqual(history.stderr, b"")


if __name__ == "__main__":
    unittest.main()
