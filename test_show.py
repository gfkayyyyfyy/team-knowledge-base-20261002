"""show 子命令的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 默认读取最新版本，``--version`` 读取指定历史版本；成功时退出码 0、
  标准错误为空，正文字节原样写入标准输出，不添加标题或换行；
- 空正文、CRLF 换行、无末尾换行的正文均按字节相等输出；
- 文档不存在、版本不存在以退出码 2 报错，标准输出为空，
  标准错误说明对应原因；
- 所选版本正文缺失、变成目录或含非法 UTF-8 时，退出码 2、标准输出为空，
  标准错误包含文档 ID、所选版本号与对应原因，不泄露 Traceback；
  默认读取受损最新版本与显式指定该版本结果一致，不回退到旧版本；
- 受损版本之外的健康版本仍可原样读取，history 仍返回全部修订；
- 失败读取不改变知识库目录条目与文件字节。

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

# 版本一：含中文、空行与末尾换行的 Markdown；版本二：不同正文
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

    def make_two_revision_doc(self) -> tuple[int, bytes, bytes]:
        """通过 add/update 建一个含两个修订版本的文档。

        版本一为含中文、空行与末尾换行的 Markdown，版本二为不同正文与标题。
        返回 (文档 ID, 版本一字节, 版本二字节)。
        """
        v1 = V1_BODY.encode("utf-8")
        v2 = V2_BODY.encode("utf-8")
        doc = self.add_doc("验收文档", v1)
        self.update_doc(doc["id"], "验收文档v2", v2)
        return doc["id"], v1, v2

    def corrupt_body(self, doc_id: int, version: int, mode: str) -> str:
        """损坏指定版本的正文文件，返回标准错误中应出现的原因。"""
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

    def show(self, doc_id: int, *extra: str) -> subprocess.CompletedProcess:
        return run_cli("--root", str(self.kb), "show", str(doc_id), *extra)

    def assert_show_ok(self, doc_id: int, expected: bytes, *extra: str) -> None:
        """断言读取成功：退出码 0、标准错误为空、输出与预期字节完全一致。"""
        result = self.show(doc_id, *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)

    def assert_show_fails(
        self,
        result: subprocess.CompletedProcess,
        doc_id: int,
        version: int,
        reason: str,
        label: str,
    ) -> None:
        """断言读取失败：退出码 2、标准输出为空、标准错误含文档、版本与原因。"""
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(str(doc_id), err, f"{label}: 应含文档 ID\n{err}")
        self.assertIn(str(version), err, f"{label}: 应含版本号\n{err}")
        self.assertIn(reason, err, f"{label}: 应说明原因 {reason}\n{err}")


class HealthyShowTests(ShowTestCase):
    """含两个修订版本的健康文档：默认读最新，--version 读历史。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc_id, self.v1, self.v2 = self.make_two_revision_doc()

    def test_default_reads_latest_version(self):
        self.assert_show_ok(self.doc_id, self.v2)

    def test_explicit_version_reads_history(self):
        self.assert_show_ok(self.doc_id, self.v1, "--version", "1")

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


class CorruptedHistoryVersionTests(ShowTestCase):
    """历史版本（v1）正文受损：读取该版本失败，最新版本不受影响。"""

    def test_corrupted_v1(self):
        for mode in ["missing", "dir", "badutf8"]:
            with self.subTest(mode=mode):
                self.kb = self.tmp / f"kb_v1_{mode}"
                doc_id, _, v2 = self.make_two_revision_doc()
                reason = self.corrupt_body(doc_id, 1, mode)
                before = snapshot(self.kb)

                result = self.show(doc_id, "--version", "1")
                self.assert_show_fails(result, doc_id, 1, reason, f"v1 {mode}")

                # 最新版本不受影响
                self.assert_show_ok(doc_id, v2)
                # 失败读取不改变知识库内容
                self.assertEqual(snapshot(self.kb), before)
                # history 仍返回两条修订
                result = run_cli("--root", str(self.kb), "history", str(doc_id))
                self.assertEqual(result.returncode, 0,
                                 result.stderr.decode("utf-8"))
                self.assertEqual(len(json.loads(result.stdout)), 2)


class CorruptedLatestVersionTests(ShowTestCase):
    """最新版本（v2）正文受损：默认读取按约定失败，历史版本不受影响。"""

    def test_corrupted_v2(self):
        for mode in ["missing", "dir", "badutf8"]:
            with self.subTest(mode=mode):
                self.kb = self.tmp / f"kb_v2_{mode}"
                doc_id, v1, _ = self.make_two_revision_doc()
                reason = self.corrupt_body(doc_id, 2, mode)
                before = snapshot(self.kb)

                # 默认读取受损最新版本失败
                default_result = self.show(doc_id)
                self.assert_show_fails(
                    default_result, doc_id, 2, reason, f"v2 {mode} 默认")
                # 显式 --version 2 结果一致，不回退到旧版本
                explicit_result = self.show(doc_id, "--version", "2")
                self.assert_show_fails(
                    explicit_result, doc_id, 2, reason, f"v2 {mode} 显式")
                self.assertEqual(explicit_result.returncode,
                                 default_result.returncode)
                self.assertEqual(explicit_result.stdout, default_result.stdout)
                self.assertEqual(explicit_result.stderr, default_result.stderr)

                # 历史版本 1 不受影响
                self.assert_show_ok(doc_id, v1, "--version", "1")
                # 失败读取不改变知识库内容
                self.assertEqual(snapshot(self.kb), before)


class ValidBodyTests(ShowTestCase):
    """合法正文原样输出：空、CRLF、无末尾换行，不添加标题或换行。"""

    CASES = {
        "empty": b"",
        "crlf": "第一行\r\n第二行\r\n".encode("utf-8"),
        "no_trailing_nl": "没有末尾换行".encode("utf-8"),
    }

    def test_valid_bodies_output_verbatim(self):
        for name, content in self.CASES.items():
            with self.subTest(name=name):
                self.kb = self.tmp / f"kb_{name}"
                doc = self.add_doc("合法正文", content)
                self.assert_show_ok(doc["id"], content)


if __name__ == "__main__":
    unittest.main()
