"""update 子命令保存修订的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 成功更新返回原文档 ID 与下一个连续版本号，标准输出为 JSON
  （按解析后的内容核对，不依赖键序或空格），退出码 0、标准错误为空；
- 完全相同的标题与正文字节也会生成独立的新修订，旧版本不被覆盖；
- 标题去除首尾空格后保存，正文（含 CRLF、无末尾换行）按字节原样保存；
- history 按版本升序返回全部修订及各自标题，show 默认读最新版本，
  显式 ``--version`` 读取的各历史版本与对应输入字节相等；
- 正文路径不存在、是目录、含非法 UTF-8 字节，或文档 ID 不存在时，
  退出码 2、标准输出为空、标准错误说明对应原因且不含 Traceback；
- 失败更新不产生修订、不占用版本号：知识库目录条目、文件字节与
  history 结果在失败前后一致，随后的合法更新得到下一个连续版本号。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_update`` 或 ``python -m unittest discover``。
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

# 版本一/二：含中文、空行与末尾换行的 UTF-8 正文（两次更新字节完全相同）
V1_BODY = "发布流程第一步：准备。\n\n发布流程第二步：验收。\n".encode("utf-8")
# 版本三：两行中文、CRLF 分隔、无末尾换行
V3_BODY = "第一行：已归档。\r\n第二行：流程结束。".encode("utf-8")

V1_TITLE = "发布流程"
V3_TITLE_RAW = "  归档说明  "
V3_TITLE = "归档说明"


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


class UpdateTestCase(unittest.TestCase):
    """基类：每个用例独立临时目录，结束后清理自身数据。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_update_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kb = self.tmp / "kb"
        self._body_seq = 0

    def _write_body(self, data: bytes) -> Path:
        self._body_seq += 1
        body_file = self.tmp / f"body_{self._body_seq}.md"
        body_file.write_bytes(data)
        return body_file

    def add_doc(self, title: str = V1_TITLE, body: bytes = V1_BODY) -> dict:
        """通过 add 命令新增只有版本一的健康文档，返回解析后的 JSON。"""
        result = run_cli(
            "--root", str(self.kb), "add",
            "--title", title, "--file", str(self._write_body(body)),
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def update_cli(self, doc_id: int, title: str, file: Path) -> subprocess.CompletedProcess:
        return run_cli(
            "--root", str(self.kb), "update", str(doc_id),
            "--title", title, "--file", str(file),
        )

    def assert_update_ok(
        self, doc_id: int, title: str, body: bytes,
        expect_version: int, expect_title: str,
    ) -> None:
        """断言更新成功：退出码 0、标准错误为空、JSON 内容符合预期。"""
        result = self.update_cli(doc_id, title, self._write_body(body))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload,
            {"id": doc_id, "version": expect_version, "title": expect_title},
        )

    def history(self, doc_id: int) -> list:
        """通过 history 命令读取修订列表，返回解析后的 JSON。"""
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def assert_show_ok(self, doc_id: int, expected: bytes, *extra: str) -> None:
        """断言读取成功：退出码 0、标准错误为空、输出与预期字节完全一致。"""
        result = run_cli("--root", str(self.kb), "show", str(doc_id), *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)


class SuccessfulUpdateTests(UpdateTestCase):
    """成功更新保留全部历史：相同内容也生成新修订，旧版本不被覆盖。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc = self.add_doc()
        self.doc_id = self.doc["id"]
        self.assertEqual(self.doc["version"], 1)

    def test_update_returns_original_id_and_next_version(self):
        # 完全相同的标题与正文字节，仍生成版本二
        self.assert_update_ok(self.doc_id, V1_TITLE, V1_BODY, 2, V1_TITLE)
        # 标题带首尾空格、正文为 CRLF 两行且无末尾换行，生成版本三
        self.assert_update_ok(self.doc_id, V3_TITLE_RAW, V3_BODY, 3, V3_TITLE)

    def test_history_lists_all_revisions_in_order(self):
        self.assert_update_ok(self.doc_id, V1_TITLE, V1_BODY, 2, V1_TITLE)
        self.assert_update_ok(self.doc_id, V3_TITLE_RAW, V3_BODY, 3, V3_TITLE)
        self.assertEqual(
            self.history(self.doc_id),
            [
                {"version": 1, "title": V1_TITLE},
                {"version": 2, "title": V1_TITLE},
                {"version": 3, "title": V3_TITLE},
            ],
        )

    def test_show_reads_each_revision_verbatim(self):
        self.assert_update_ok(self.doc_id, V1_TITLE, V1_BODY, 2, V1_TITLE)
        self.assert_update_ok(self.doc_id, V3_TITLE_RAW, V3_BODY, 3, V3_TITLE)
        # 默认输出最新版本（版本三）
        self.assert_show_ok(self.doc_id, V3_BODY)
        # 显式读取各历史版本，与对应输入字节相等
        self.assert_show_ok(self.doc_id, V1_BODY, "--version", "1")
        self.assert_show_ok(self.doc_id, V1_BODY, "--version", "2")
        self.assert_show_ok(self.doc_id, V3_BODY, "--version", "3")

    def test_identical_revisions_kept_separately(self):
        self.assert_update_ok(self.doc_id, V1_TITLE, V1_BODY, 2, V1_TITLE)
        # 两次相同内容的修订分别落盘，互不覆盖
        v1_file = self.kb / "bodies" / str(self.doc_id) / "v1.md"
        v2_file = self.kb / "bodies" / str(self.doc_id) / "v2.md"
        self.assertEqual(v1_file.read_bytes(), V1_BODY)
        self.assertEqual(v2_file.read_bytes(), V1_BODY)
        # 再更新后旧标题与旧正文不被新版本覆盖
        self.assert_update_ok(self.doc_id, V3_TITLE_RAW, V3_BODY, 3, V3_TITLE)
        self.assertEqual(v1_file.read_bytes(), V1_BODY)
        self.assertEqual(v2_file.read_bytes(), V1_BODY)
        self.assertEqual(self.history(self.doc_id)[0]["title"], V1_TITLE)
        self.assertEqual(self.history(self.doc_id)[1]["title"], V1_TITLE)


class FailedUpdateTests(UpdateTestCase):
    """无效更新：退出码 2、不产生修订、不占用版本号。"""

    def setUp(self) -> None:
        super().setUp()
        self.doc = self.add_doc()
        self.doc_id = self.doc["id"]

    def assert_failed_update(
        self, result: subprocess.CompletedProcess, reason: str, label: str,
    ) -> None:
        """断言更新失败：退出码 2、标准输出为空、标准错误说明原因。"""
        self.assertEqual(result.returncode, 2,
                         f"{label}: 退出码 {result.returncode}")
        self.assertEqual(result.stdout, b"",
                         f"{label}: 标准输出应为空: {result.stdout!r}")
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(reason, err, f"{label}: 应说明原因 {reason}\n{err}")

    def assert_no_revision_created(self, before: list, label: str) -> None:
        """断言失败未产生修订：目录内容不变、history 不变、版本号未跳号。"""
        self.assertEqual(snapshot(self.kb), before,
                         f"{label}: 知识库目录条目与文件字节应保持一致")
        self.assertEqual(self.history(self.doc_id),
                         [{"version": 1, "title": V1_TITLE}],
                         f"{label}: history 应仍只有版本一")
        # 随后的合法更新得到版本二，证明失败没有占用版本号
        self.assert_update_ok(self.doc_id, V1_TITLE, V1_BODY, 2, V1_TITLE)

    def test_missing_body_file(self):
        before = snapshot(self.kb)
        missing = self.tmp / "不存在的正文.md"
        result = self.update_cli(self.doc_id, V1_TITLE, missing)
        self.assert_failed_update(result, "不存在或不是普通文件", "正文不存在")
        self.assert_no_revision_created(before, "正文不存在")

    def test_body_path_is_directory(self):
        before = snapshot(self.kb)
        body_dir = self.tmp / "正文目录"
        body_dir.mkdir()
        result = self.update_cli(self.doc_id, V1_TITLE, body_dir)
        self.assert_failed_update(result, "不存在或不是普通文件", "正文是目录")
        self.assert_no_revision_created(before, "正文是目录")

    def test_body_not_utf8(self):
        bad_file = self.tmp / "非法编码.md"
        bad_file.write_bytes(b"\xff\xfe invalid \x80")
        before = snapshot(self.kb)
        result = self.update_cli(self.doc_id, V1_TITLE, bad_file)
        self.assert_failed_update(result, "UTF-8", "非法 UTF-8 正文")
        self.assert_no_revision_created(before, "非法 UTF-8 正文")

    def test_missing_document(self):
        before = snapshot(self.kb)
        result = self.update_cli(999, V1_TITLE, self._write_body(V1_BODY))
        self.assert_failed_update(result, "文档不存在", "文档不存在")
        self.assert_no_revision_created(before, "文档不存在")


if __name__ == "__main__":
    unittest.main()
