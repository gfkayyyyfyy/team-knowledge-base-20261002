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
- 索引文件不是有效 SQLite 数据库、或数据库缺少读取正文所需的
  documents/versions 表时，默认读取与显式 ``--version`` 读取统一以
  退出码 2 报错，标准输出为空，标准错误包含“索引无法打开或查询失败”、
  文档 ID（显式版本还含版本号），不泄露 Traceback，不误报为文档或版本
  不存在，不从正文目录猜测版本；
- 受损版本之外的健康版本仍可原样读取，history 仍返回全部修订；
- 失败读取不改变知识库目录条目与文件字节。

每个用例使用独立临时目录并在结束后清理，可离线重复运行。
执行方式：``python -m unittest test_show`` 或 ``python -m unittest discover``。
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


class IndexUnavailableTests(ShowTestCase):
    """索引打开或查询失败的读取约定。

    与 HealthyShowTests 对照：根目录不存在或目录尚无索引属于“尚未初始化”，
    按文档不存在报错；而已有索引文件不是有效 SQLite 数据库、或数据库缺少
    读取正文所需的 documents/versions 表属于“索引读取失败”，默认读取与
    显式 --version 读取都必须以退出码 2 报错，标准输出为空（不输出空正文
    冒充成功，也不从正文目录猜测版本或回退其他版本），标准错误含
    “索引无法打开或查询失败”与文档 ID（显式版本还含版本号），不泄露
    Traceback，不误报为文档或版本不存在，失败前后目录条目与文件字节一致。
    """

    DOC_ID = 1
    REASON = "索引无法打开或查询失败"

    def assert_index_failure(
        self, extra: tuple[str, ...], expect_version: int | None, label: str
    ) -> None:
        """断言一次 show 读取按索引读取失败结束，且不改变知识库。"""
        before = snapshot(self.kb)
        result = self.show(self.DOC_ID, *extra)

        self.assertEqual(result.returncode, 2, f"{label}: 退出码应为 2")
        self.assertEqual(
            result.stdout, b"",
            f"{label}: 标准输出应为空（不得输出空正文冒充成功）: "
            f"{result.stdout!r}",
        )
        err = result.stderr.decode("utf-8")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")
        self.assertIn(self.REASON, err, f"{label}: 应说明索引读取失败\n{err}")
        self.assertIn(f"文档 {self.DOC_ID}", err,
                      f"{label}: 应含请求的文档 ID\n{err}")
        if expect_version is not None:
            self.assertIn(f"的版本 {expect_version}", err,
                          f"{label}: 显式版本应含请求的版本号\n{err}")
        # 索引故障不得误报为文档或版本不存在
        self.assertNotIn("文档不存在", err,
                         f"{label}: 不应误报文档不存在\n{err}")
        self.assertNotIn("版本不存在", err,
                         f"{label}: 不应误报版本不存在\n{err}")
        # 失败读取不新增、不改写任何目录条目或文件字节
        self.assertEqual(snapshot(self.kb), before, f"{label}: 失败读取改写了知识库")

    def prepare_plain_text_index(self) -> None:
        """场景一：knowledge_base.sqlite3 是普通 UTF-8 文本。"""
        self.kb.mkdir()
        (self.kb / "knowledge_base.sqlite3").write_text(
            "这不是 SQLite 数据库，只是普通 UTF-8 文本。\n", encoding="utf-8"
        )

    def prepare_documents_only_index(self) -> None:
        """场景二：有效 SQLite 索引，只有 documents 表且含 ID=1 记录。"""
        self.kb.mkdir()
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT)"
                )
                conn.execute("INSERT INTO documents (id) VALUES (1)")
        finally:
            conn.close()

    def prepare_no_tables_index(self) -> None:
        """补充场景：可打开的 SQLite 数据库但没有任何业务表。"""
        self.kb.mkdir()
        conn = sqlite3.connect(self.kb / "knowledge_base.sqlite3")
        conn.close()

    def test_plain_text_index_default_and_explicit_version(self):
        # 验收场景一：普通 UTF-8 文本充当索引，默认与 --version 1 两次读取
        self.prepare_plain_text_index()
        self.assert_index_failure((), None, "纯文本索引 默认读取")
        self.assert_index_failure(
            ("--version", "1"), 1, "纯文本索引 显式版本 1"
        )

    def test_documents_only_index_default_and_explicit_version(self):
        # 验收场景二：documents 表含 ID=1 但缺 versions 表
        self.prepare_documents_only_index()
        self.assert_index_failure((), None, "缺 versions 表 默认读取")
        self.assert_index_failure(
            ("--version", "1"), 1, "缺 versions 表 显式版本 1"
        )

    def test_index_without_documents_table(self):
        # 补充：数据库可打开但连 documents 表也没有，同样属于索引读取失败
        self.prepare_no_tables_index()
        self.assert_index_failure((), None, "空数据库 默认读取")
        self.assert_index_failure(
            ("--version", "1"), 1, "空数据库 显式版本 1"
        )

    def test_other_version_number_is_reported(self):
        # 显式请求其他版本号时，错误信息应原样带上该版本号
        self.prepare_documents_only_index()
        self.assert_index_failure(
            ("--version", "7"), 7, "缺 versions 表 显式版本 7"
        )

    def test_queryable_index_still_reports_missing_document(self):
        # 对照：索引本身可查询时，不存在的文档仍按既有“文档不存在”语义，
        # 不能因为缺少 versions 数据就一律算索引故障
        self.prepare_documents_only_index()
        result = self.show(999)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        err = result.stderr.decode("utf-8")
        self.assertIn("文档不存在", err)
        self.assertNotIn(self.REASON, err)
        self.assertNotIn("Traceback", err)

    def test_missing_root_still_reports_missing_document(self):
        # 对照：根目录不存在继续按文档不存在报错，且不初始化目录
        missing = self.tmp / "no_such_dir"
        result = run_cli(
            "--root", str(missing), "show", str(self.DOC_ID)
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        err = result.stderr.decode("utf-8")
        self.assertIn("文档不存在", err)
        self.assertNotIn(self.REASON, err)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
