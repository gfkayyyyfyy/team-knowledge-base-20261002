"""update 子命令保存修订的命令行回归测试。

通过公开入口 ``python -m knowledge_base`` 观察退出码、标准输出与标准错误，
固定以下公开行为：

- 成功更新保留原文档 ID，版本号连续递增，内容完全相同也生成独立修订；
- 标题去除首尾空白后保存，成功时退出码 0、标准错误为空，
  标准输出为 ``{"id", "version", "title"}`` 的 JSON，按解析后的内容核对；
- history 按版本升序返回全部修订及各自标题，旧标题不被新标题覆盖；
- show 默认输出版本三，显式读取版本一/二/三均与对应输入字节相等，
  CRLF、空行与末尾换行按字节保留；
- 正文路径不存在、指向目录、含非法 UTF-8 字节，或文档 ID 不存在时，
  以退出码 2 结束，标准输出为空，标准错误分别说明
  正文不存在或不是普通文件、UTF-8 解码失败、文档不存在，不泄露 Traceback；
- 失败更新不生成修订、不占用版本号：知识库目录条目、文件字节与 history
  在失败前后一致，随后对原文档的合法更新得到版本二。

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

# 版本一/二的相同正文：含中文、空行与末尾换行
V1_TITLE = "发布流程"
V1_BODY = (
    "# 发布流程\n"
    "\n"
    "发布前先跑一遍回归测试：\n"
    "\n"
    "- 全部通过再合并\n"
    "- 保留每次修订记录\n"
).encode("utf-8")

# 版本三：标题首尾带空格（应被去除），正文为两行中文、CRLF 分隔、无末尾换行
V3_TITLE_RAW = "  归档说明  "
V3_TITLE = "归档说明"
V3_BODY = "归档说明第一行：旧版本只读保留\r\n归档说明第二行：以此版本为准".encode(
    "utf-8"
)

MISSING_DOC_ID = 999


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
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"id", "version", "title"})
        return payload

    def history(self, doc_id: int) -> list:
        """通过 history 命令读取修订记录，返回解析后的列表。"""
        result = run_cli("--root", str(self.kb), "history", str(doc_id))
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def show_bytes(self, doc_id: int, *extra: str) -> bytes:
        """通过 show 命令读取正文，断言成功并返回原始字节。"""
        result = run_cli("--root", str(self.kb), "show", str(doc_id), *extra)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stderr, b"")
        return result.stdout


class SuccessfulUpdateTests(UpdateTestCase):
    """发布流程 文档的三次修订：相同内容一次 + 标题正文变更一次。"""

    def setUp(self) -> None:
        super().setUp()
        first = self.add_doc(V1_TITLE, V1_BODY)
        self.doc_id = first["id"]
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["title"], V1_TITLE)

        # 第一次更新：标题与正文字节完全相同
        self.result_v2 = self.update_doc(self.doc_id, V1_TITLE, V1_BODY)
        # 第二次更新：首尾带空格的标题 + 两行 CRLF、无末尾换行的正文
        self.result_v3 = self.update_doc(self.doc_id, V3_TITLE_RAW, V3_BODY)

    def test_identical_update_keeps_id_and_makes_version_two(self):
        self.assertEqual(
            self.result_v2,
            {"id": self.doc_id, "version": 2, "title": V1_TITLE},
        )

    def test_changed_update_makes_version_three_with_stripped_title(self):
        self.assertEqual(
            self.result_v3,
            {"id": self.doc_id, "version": 3, "title": V3_TITLE},
        )

    def test_history_lists_three_revisions_ascending_with_titles(self):
        self.assertEqual(
            self.history(self.doc_id),
            [
                {"version": 1, "title": V1_TITLE},
                {"version": 2, "title": V1_TITLE},
                {"version": 3, "title": V3_TITLE},
            ],
        )

    def test_show_default_outputs_version_three(self):
        self.assertEqual(self.show_bytes(self.doc_id), V3_BODY)

    def test_show_each_version_matches_input_bytes(self):
        # 版本一与版本二内容相同但为两次独立修订，分别按原字节存在
        self.assertEqual(self.show_bytes(self.doc_id, "--version", "1"), V1_BODY)
        self.assertEqual(self.show_bytes(self.doc_id, "--version", "2"), V1_BODY)
        self.assertEqual(self.show_bytes(self.doc_id, "--version", "3"), V3_BODY)

    def test_identical_revisions_stored_as_separate_files(self):
        bodies = self.kb / "bodies" / str(self.doc_id)
        self.assertEqual(
            sorted(p.name for p in bodies.iterdir()),
            ["v1.md", "v2.md", "v3.md"],
        )
        self.assertEqual((bodies / "v1.md").read_bytes(), V1_BODY)
        self.assertEqual((bodies / "v2.md").read_bytes(), V1_BODY)
        self.assertEqual((bodies / "v3.md").read_bytes(), V3_BODY)
        # 两个版本为独立文件（非硬链接），各自字节互不覆盖
        self.assertFalse((bodies / "v1.md").samefile(bodies / "v2.md"))

    def test_old_title_and_body_survive_new_version(self):
        # 新版本的标题与正文不覆盖历史修订
        records = self.history(self.doc_id)
        self.assertEqual(records[0], {"version": 1, "title": V1_TITLE})
        self.assertEqual(records[1], {"version": 2, "title": V1_TITLE})
        self.assertEqual(
            self.show_bytes(self.doc_id, "--version", "1"), V1_BODY
        )
        self.assertEqual(
            self.show_bytes(self.doc_id, "--version", "3"), V3_BODY
        )


class FailedUpdateTests(UpdateTestCase):
    """无效更新：退出码 2、标准输出为空、不生成修订、不占用版本号。

    每种情形只设置一个无效条件，并使用各自独立的知识库目录。
    """

    EXPECTED_HISTORY_AFTER_ADD = [{"version": 1, "title": V1_TITLE}]

    def assert_failed_update(
        self, target_id: int, body_arg: str, reason: str, label: str
    ) -> None:
        """运行一次无效更新并固定失败约定、存储不变性与版本号不跳号。"""
        before_files = snapshot(self.kb)
        before_history = self.history(self.doc_id)

        result = run_cli(
            "--root", str(self.kb), "update", str(target_id),
            "--title", "不应生效的标题", "--file", body_arg,
        )

        self.assertEqual(result.returncode, 2, f"{label}: 退出码应为 2")
        self.assertEqual(
            result.stdout, b"", f"{label}: 标准输出应为空: {result.stdout!r}"
        )
        err = result.stderr.decode("utf-8")
        self.assertIn(reason, err, f"{label}: 应说明原因 {reason}\n{err}")
        self.assertNotIn("Traceback", err, f"{label}: 不应泄露堆栈\n{err}")

        # 失败前后知识库目录条目、文件字节与 history 完全一致；
        # 目标文档不存在时，原文档同样保持只有版本一
        self.assertEqual(snapshot(self.kb), before_files)
        self.assertEqual(self.history(self.doc_id), before_history)
        self.assertEqual(before_history, self.EXPECTED_HISTORY_AFTER_ADD)
        # 最新正文仍是版本一，失败没有写入半修订
        self.assertEqual(self.show_bytes(self.doc_id), V1_BODY)

        # 随后对原文档的合法更新得到版本二：失败未生成修订或造成版本跳号
        recovered = self.update_doc(self.doc_id, "合法恢复更新", V3_BODY)
        self.assertEqual(
            recovered,
            {"id": self.doc_id, "version": 2, "title": "合法恢复更新"},
        )
        self.assertEqual(
            self.history(self.doc_id),
            [
                {"version": 1, "title": V1_TITLE},
                {"version": 2, "title": "合法恢复更新"},
            ],
        )
        self.assertEqual(self.show_bytes(self.doc_id, "--version", "1"), V1_BODY)
        self.assertEqual(self.show_bytes(self.doc_id), V3_BODY)

    def test_invalid_updates(self):
        # 正文路径不存在
        missing_body = self.tmp / "missing_body.md"
        # 正文路径是目录
        body_dir = self.tmp / "body_dir"
        body_dir.mkdir()
        # 普通文件含非法 UTF-8 字节
        bad_file = self.tmp / "bad_utf8.md"
        bad_file.write_bytes(b"\xff\xfe\x80 not valid utf-8")
        # 合法正文件搭配不存在的正整数文档 ID
        valid_file = self._write_body("合法的更新正文。\n".encode("utf-8"))

        cases = [
            ("missing_body", None, str(missing_body),
             "正文文件不存在或不是普通文件"),
            ("body_is_dir", None, str(body_dir),
             "正文文件不存在或不是普通文件"),
            ("bad_utf8", None, str(bad_file), "UTF-8"),
            ("missing_doc", MISSING_DOC_ID, str(valid_file), "文档不存在"),
        ]

        for label, target, body_arg, reason in cases:
            with self.subTest(mode=label):
                self.kb = self.tmp / f"kb_{label}"
                doc = self.add_doc(V1_TITLE, V1_BODY)
                self.doc_id = doc["id"]
                target_id = self.doc_id if target is None else target
                self.assert_failed_update(target_id, body_arg, reason, label)


if __name__ == "__main__":
    unittest.main()
