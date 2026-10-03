"""show 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base --root DIR`` 观察退出码、
标准输出与标准错误，固定以下公开行为：

- 默认读取最新版本，``--version`` 读取指定历史版本，正文按原始字节
  原样输出，不添加标题或换行，成功时退出码 0 且标准错误为空；
- 空正文、CRLF 换行、无末尾换行的正文均按字节相等输出；
- 文档不存在、版本不存在时退出码 2、标准输出为空，标准错误说明原因；
- 所选版本的正文缺失、变成目录或含非法 UTF-8 时，退出码 2、标准输出
  为空，标准错误包含文档 ID、所选版本号与原因且不泄露 Traceback；
  默认读取受损最新版本与显式指定该版本结果一致，不回退到旧版本；
- 受损版本的另一健康版本仍可原样读取，history 仍返回全部修订，
  失败读取不改变知识库目录条目与文件字节。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_show`` 或 ``python -m unittest discover``。
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

# 版本一：含中文、空行与末尾换行的 Markdown；版本二：不同正文与标题
V1_BODY = "# 标题\n\n中文正文第一段。\n\n- 列表项\n"
V2_BODY = "第二版正文 different body\n"


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


class ShowTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_show_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, data: bytes) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_bytes(data)
        return body_file

    def _add(self, title: str, data: bytes) -> int:
        """通过 add 命令新增文档，返回文档 ID。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(data)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        return json.loads(result.stdout)["id"]

    def _update(self, doc_id: int, title: str, data: bytes) -> None:
        """通过 update 命令更新文档，生成下一个版本。"""
        result = run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", title, "--file", str(self._write_body(data)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))

    def make_two_revision_doc(self) -> tuple[int, bytes, bytes]:
        """通过公开命令创建含两个修订版本的文档，返回 (ID, v1 字节, v2 字节)。"""
        v1 = V1_BODY.encode("utf-8")
        v2 = V2_BODY.encode("utf-8")
        doc_id = self._add("验收文档", v1)
        self._update(doc_id, "验收文档v2", v2)
        return doc_id, v1, v2

    def show(self, doc_id: int, *extra: str) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "show", str(doc_id), *extra)

    def assert_show_ok(self, result: subprocess.CompletedProcess,
                       expected: bytes) -> None:
        """断言读取成功：退出码 0、标准错误为空、输出与正文字节完全一致。"""
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)

    def assert_show_fails(self, result: subprocess.CompletedProcess,
                          doc_id: int, version: int, reason: str) -> None:
        """断言读取失败：退出码 2、标准输出为空、标准错误含 ID、版本号与原因。"""
        self.assertEqual(result.returncode, 2,
                         f"退出码应为 2: {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"不应泄露堆栈:\n{err}")
        self.assertIn(str(doc_id), err, f"标准错误应含文档 ID:\n{err}")
        self.assertIn(str(version), err, f"标准错误应含版本号:\n{err}")
        self.assertIn(reason, err, f"标准错误应说明原因 {reason}:\n{err}")

    def body_path(self, doc_id: int, version: int) -> Path:
        return self.kb / "bodies" / str(doc_id) / f"v{version}.md"

    def assert_history_has_two_revisions(self, doc_id: int) -> None:
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(len(json.loads(result.stdout)), 2)


class HealthyShowTests(ShowTestCase):
    """健康知识库：默认读最新版本，--version 读历史版本，错误类别不变。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id, self.v1, self.v2 = self.make_two_revision_doc()

    def test_default_reads_latest_version(self):
        self.assert_show_ok(self.show(self.doc_id), self.v2)

    def test_explicit_version_reads_original_bytes(self):
        self.assert_show_ok(self.show(self.doc_id, "--version", "1"), self.v1)

    def test_missing_document(self):
        result = self.show(999)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn("文档不存在", result.stderr.decode("utf-8"))

    def test_missing_version(self):
        result = self.show(self.doc_id, "--version", "99")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn("版本不存在", result.stderr.decode("utf-8"))


class ValidBodyTests(ShowTestCase):
    """合法正文原样输出：空正文、CRLF 换行、无末尾换行，不添加标题或换行。"""

    def assert_body_roundtrip(self, content: bytes) -> None:
        doc_id = self._add("合法正文", content)
        self.assert_show_ok(self.show(doc_id), content)

    def test_empty_body(self):
        self.assert_body_roundtrip(b"")

    def test_crlf_line_endings(self):
        self.assert_body_roundtrip("第一行\r\n第二行\r\n".encode("utf-8"))

    def test_no_trailing_newline(self):
        self.assert_body_roundtrip("没有末尾换行".encode("utf-8"))


class CorruptedBodyTests(ShowTestCase):
    """正文受损时的读取约定。

    历史版本（v1）或最新版本（v2）的正文缺失、变成目录或含非法 UTF-8 时，
    读取该版本统一以退出码 2 失败、标准输出为空、不泄露 Traceback；
    默认读取受损最新版本与显式指定该版本结果一致，不回退到旧版本；
    另一健康版本仍可原样读取，history 仍返回两条修订，
    失败读取前后的知识库目录条目与文件字节保持一致。
    """

    CORRUPTIONS = {
        "missing": "缺失或不是普通文件",
        "dir": "缺失或不是普通文件",
        "badutf8": "UTF-8",
    }

    def _corrupt(self, path: Path, kind: str) -> None:
        if kind == "missing":
            path.unlink()
        elif kind == "dir":
            path.unlink()
            path.mkdir()
        else:
            path.write_bytes(b"\xff\xfe invalid \x80")

    def test_corrupted_v1_body(self):
        for kind, reason in self.CORRUPTIONS.items():
            with self.subTest(corruption=kind):
                doc_id, _, v2 = self.make_two_revision_doc()
                self._corrupt(self.body_path(doc_id, 1), kind)
                before = snapshot(self.kb)

                self.assert_show_fails(
                    self.show(doc_id, "--version", "1"), doc_id, 1, reason)
                # 最新版本不受影响，仍可原样读取
                self.assert_show_ok(self.show(doc_id), v2)
                # history 仍返回原有两条修订
                self.assert_history_has_two_revisions(doc_id)
                # 失败读取不改变知识库内容
                self.assertEqual(snapshot(self.kb), before)

    def test_corrupted_v2_body(self):
        for kind, reason in self.CORRUPTIONS.items():
            with self.subTest(corruption=kind):
                doc_id, v1, _ = self.make_two_revision_doc()
                self._corrupt(self.body_path(doc_id, 2), kind)
                before = snapshot(self.kb)

                # 默认读取受损最新版本按约定失败
                self.assert_show_fails(self.show(doc_id), doc_id, 2, reason)
                # 显式 --version 2 结果一致，不回退到旧版本
                self.assert_show_fails(
                    self.show(doc_id, "--version", "2"), doc_id, 2, reason)
                # 历史版本 1 不受影响，仍可原样读取
                self.assert_show_ok(self.show(doc_id, "--version", "1"), v1)
                # history 仍返回原有两条修订
                self.assert_history_has_two_revisions(doc_id)
                # 失败读取不改变知识库内容
                self.assertEqual(snapshot(self.kb), before)


if __name__ == "__main__":
    unittest.main()
