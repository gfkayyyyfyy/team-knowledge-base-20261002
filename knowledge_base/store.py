"""存储层：正文以 UTF-8 文本文件保存，SQLite 保存文档与版本索引。"""

from __future__ import annotations

import difflib
import os
import sqlite3
from pathlib import Path

DB_FILENAME = "knowledge_base.sqlite3"
BODIES_DIRNAME = "bodies"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE TABLE IF NOT EXISTS versions (
    doc_id INTEGER NOT NULL REFERENCES documents (id),
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body_path TEXT NOT NULL,
    PRIMARY KEY (doc_id, version)
);
"""


class KBError(Exception):
    """可预期的业务错误，命令行统一以退出码 2 报告。"""


def read_body_file(file_arg: str) -> bytes:
    """读取正文文件，校验存在性、普通文件与 UTF-8 可解码性。"""
    path = Path(file_arg)
    if not path.is_file():
        raise KBError(f"正文文件不存在或不是普通文件: {file_arg}")
    data = path.read_bytes()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise KBError(f"正文无法按 UTF-8 解码: {file_arg}")
    return data


def _split_lines_keepends(text: str) -> list[str]:
    """仅以 LF 划分行并保留行尾换行；空文本返回空列表。

    不使用 str.splitlines，避免把单独的 \\r 等字符当作行边界；
    只有最末一行可能不带换行符。
    """
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def unified_body_diff(
    old: bytes, new: bytes, from_label: str, to_label: str
) -> str:
    """生成两段 UTF-8 正文的统一差异文本，正文相同时返回空串。

    比较前把 CRLF 规范化为 LF，输出统一使用 LF；每处变化最多保留
    前后三行上下文，邻近变化按统一差异格式合并。文件头即 from_label
    与 to_label，不附加标题、绝对路径或时间戳。末行是否带换行仍算
    差异，缺少末尾换行的行之后追加 "\\ No newline at end of file"。
    其余内容（中文、空行、行内空白、Markdown 符号）按原文比较。
    """
    old_text = old.decode("utf-8").replace("\r\n", "\n")
    new_text = new.decode("utf-8").replace("\r\n", "\n")
    old_lines = _split_lines_keepends(old_text)
    new_lines = _split_lines_keepends(new_text)
    parts: list[str] = []
    for line in difflib.unified_diff(
        old_lines, new_lines, fromfile=from_label, tofile=to_label, n=3
    ):
        if line.endswith("\n"):
            parts.append(line)
        else:
            # 仅文件末行可能不带换行符，按统一差异约定补换行并加标示
            parts.append(line + "\n")
            parts.append("\\ No newline at end of file\n")
    return "".join(parts)


class Store:
    """以 --root 指定目录为知识库根目录的文档存储。"""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.db_path = self.root / DB_FILENAME
        self.bodies_dir = self.root / BODIES_DIRNAME

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self) -> None:
        """首次成功新增时初始化目录与索引。"""
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _require_initialized(self, doc_id: int) -> None:
        if not self.db_path.is_file():
            raise KBError(f"文档不存在: {doc_id}")

    def _require_document(self, conn: sqlite3.Connection, doc_id: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise KBError(f"文档不存在: {doc_id}")

    def _write_body(self, doc_id: int, version: int, data: bytes) -> str:
        """把正文写入 bodies/<id>/v<version>.md，返回相对根目录的路径。"""
        rel = Path(BODIES_DIRNAME) / str(doc_id) / f"v{version}.md"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel.as_posix()

    def add(self, title: str, data: bytes) -> tuple[int, int]:
        """新增文档，返回 (文档 ID, 版本号)，版本从 1 开始。"""
        self._init()
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO documents DEFAULT VALUES")
            doc_id = cur.lastrowid
            version = 1
            rel = self._write_body(doc_id, version, data)
            conn.execute(
                "INSERT INTO versions (doc_id, version, title, body_path)"
                " VALUES (?, ?, ?, ?)",
                (doc_id, version, title, rel),
            )
        return doc_id, version

    def update(self, doc_id: int, title: str, data: bytes) -> int:
        """更新已有文档，生成下一个连续版本，返回新版本号。

        更新前需确认文档并读取当前版本：索引文件无法作为 SQLite 打开，
        或缺少 documents、versions 表而无法完成这两步查询时，以 KBError
        报为索引读取失败（错误信息含文档 ID），不误报为文档不存在，不
        补建表或重建索引，也不写入本次正文、不占用版本号。数据库正常
        但文档不存在时仍报“文档不存在”。正文写入阶段的故障恢复不在
        本方法的只读保证范围内。
        """
        self._require_initialized(doc_id)
        try:
            with self._connect() as conn:
                self._require_document(conn, doc_id)
                row = conn.execute(
                    "SELECT MAX(version) FROM versions WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                version = (row[0] or 0) + 1
                rel = self._write_body(doc_id, version, data)
                conn.execute(
                    "INSERT INTO versions (doc_id, version, title, body_path)"
                    " VALUES (?, ?, ?, ?)",
                    (doc_id, version, title, rel),
                )
        except sqlite3.Error as exc:
            raise KBError(
                f"索引无法打开或查询失败: 文档 {doc_id}: {exc}"
            ) from exc
        return version

    def get_body(self, doc_id: int, version: int | None = None) -> bytes:
        """读取指定版本正文；version 为 None 时读取最新版本。

        所选正文文件缺失、不是普通文件或无法按 UTF-8 解码时抛出 KBError，
        不回退到其他版本。索引文件无法作为 SQLite 打开，或缺少查询所需的
        documents、versions 表时，同样以 KBError 报为索引读取失败（错误信息
        含文档 ID；显式指定版本时还含请求的版本号），不误报为文档或版本
        不存在。
        """
        self._require_initialized(doc_id)
        try:
            with self._connect() as conn:
                self._require_document(conn, doc_id)
                if version is None:
                    row = conn.execute(
                        "SELECT version, body_path FROM versions WHERE doc_id = ?"
                        " ORDER BY version DESC LIMIT 1",
                        (doc_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT version, body_path FROM versions WHERE doc_id = ?"
                        " AND version = ?",
                        (doc_id, version),
                    ).fetchone()
        except sqlite3.Error as exc:
            if version is None:
                detail = f"文档 {doc_id}"
            else:
                detail = f"文档 {doc_id} 的版本 {version}"
            raise KBError(f"索引无法打开或查询失败: {detail}: {exc}") from exc
        if row is None:
            raise KBError(f"版本不存在: 文档 {doc_id} 的版本 {version}")
        selected_version, rel = row
        path = self.root / rel
        if not path.is_file():
            raise KBError(
                f"正文文件缺失或不是普通文件: 文档 {doc_id} 的版本"
                f" {selected_version} ({rel})"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KBError(
                f"正文无法读取: 文档 {doc_id} 的版本"
                f" {selected_version} ({rel}): {exc}"
            ) from exc
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise KBError(
                f"正文无法按 UTF-8 解码: 文档 {doc_id} 的版本"
                f" {selected_version} ({rel})"
            )
        return data

    def export_body(self, doc_id: int, version: int | None, output: str) -> None:
        """把所选版本正文逐字节导出到 output 指定的本地文件。

        目标路径按调用时的工作目录解释（相对或绝对均可），不强制扩展名；
        父目录必须已经存在且是目录，导出功能不创建目录。目标已存在时一律
        拒绝（无论文件还是目录、内容是否相同，包括指向知识库已有正文）。
        写入使用排他创建，写入失败时清理本次新建的文件，不改动已有目标。

        根目录不存在或不是目录、索引缺失或无法查询、文档或版本不存在、
        源正文缺失/不是普通文件/无法读取/不是 UTF-8 时抛出 KBError，不
        回退到其他版本。本方法不修改源正文、索引与历史，不生成新版本，
        也不初始化知识库。
        """
        if not self.root.is_dir():
            raise KBError(f"知识库根目录不存在或不是目录: {self.root}")
        if not self.db_path.is_file():
            raise KBError(f"索引缺失，无法查询: {self.db_path}")
        body = self.get_body(doc_id, version)
        target = Path(output)
        if os.path.lexists(target):
            raise KBError(f"导出目标已存在: {output}")
        if not target.parent.is_dir():
            raise KBError(f"导出目标的父目录不存在或不是目录: {output}")
        try:
            with open(target, "xb") as fh:
                fh.write(body)
        except FileExistsError:
            # 排他创建发现目标已存在（如并发创建），不触碰该目标
            raise KBError(f"导出目标已存在: {output}") from None
        except OSError as exc:
            # 写入失败时清理本次可能新建的不完整文件，不留下残留
            try:
                target.unlink()
            except OSError:
                pass
            raise KBError(f"导出目标无法写入: {output}: {exc}") from exc

    def diff(self, doc_id: int, from_version: int, to_version: int) -> str:
        """对比同一文档两个版本的正文，返回统一差异文本（可能为空串）。

        from_version 为差异旧侧，to_version 为新侧，允许逆序，也允许
        两者相同；即使版本号相同，两侧也各自完整执行存在性与可读性
        校验，不跳过损坏检查。文件头形如 "<id>/v<N>.md"。正文相同
        （含仅标题不同）时返回空串，不输出文件头。

        文档或任一版本不存在、根路径不是目录、索引无法查询，以及任一
        所选正文缺失、不是普通文件或不能按 UTF-8 解码时抛出 KBError，
        不产出半份差异，也不回退到其他版本。根目录不存在或尚无索引
        同样报错，不创建目录或索引。本方法只读，不改变已有正文、索引
        和历史，不生成新版本。
        """
        if self.root.exists() and not self.root.is_dir():
            raise KBError(f"知识库根路径不是目录: {self.root}")
        try:
            old = self.get_body(doc_id, from_version)
            new = self.get_body(doc_id, to_version)
        except sqlite3.Error as exc:
            raise KBError(f"索引无法打开或查询失败: {exc}") from exc
        return unified_body_diff(
            old,
            new,
            f"{doc_id}/v{from_version}.md",
            f"{doc_id}/v{to_version}.md",
        )

    def history(self, doc_id: int) -> list[dict]:
        """按版本号升序返回 [{version, title}, ...]，不产生修订。

        索引文件无法作为 SQLite 打开，或缺少查询所需的 documents、versions
        表时，以 KBError 报为索引读取失败（错误信息含文档 ID），不误报为
        文档不存在，不返回空数组或部分历史，也不修复或重建索引。数据库
        正常但文档不存在时仍报“文档不存在”。
        """
        self._require_initialized(doc_id)
        try:
            with self._connect() as conn:
                self._require_document(conn, doc_id)
                rows = conn.execute(
                    "SELECT version, title FROM versions WHERE doc_id = ?"
                    " ORDER BY version ASC",
                    (doc_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise KBError(f"索引无法打开或查询失败: 文档 {doc_id}: {exc}") from exc
        return [{"version": version, "title": title} for version, title in rows]

    def search(
        self,
        query: str,
        sort: str = "id",
        match: str = "contains",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """按每篇文档的最新标题做忽略大小写的字面匹配。

        匹配在 Python 端用 str.casefold 完成，%、_、*、[、] 等均为普通
        字符，不读取正文与历史标题。返回 [{id, version, title}, ...]，
        标题保留原文，不产生任何修订或文件。

        match 为 "contains"（默认）时，最新标题字面子串包含查询词即命中；
        为 "exact" 时，只在最新标题与查询词经 casefold 后完整相等时命中，
        以查询词开头或在中间包含均不算命中。两种匹配都只比较查询词去除
        首尾空白后的文本，内部空白原样参与比较。

        sort 为 "id"（默认）时按文档 ID 升序；为 "relevance" 时仅对
        contains 命中按标题匹配程度分组：完全相等的最前，以查询词开头的
        其次，其余包含查询词的最后，同组内按文档 ID 升序。分组沿用与匹配
        相同的 casefold 语义，不按出现次数、标题长度或版本号再排序。
        exact 命中全部是完整相等，"relevance" 与 "id" 一样按文档 ID 升序。

        limit 为正整数时，在最终排序与偏移完成后只保留前 limit 条；命中
        不足时返回全部剩余命中，不补空项。limit 为 None 时不设上限。

        offset 为非负整数时，在最终排序完成后先跳过前 offset 条命中，
        再应用 limit；offset 统计的是筛选后的命中条数而非文档 ID。偏移量
        等于或超过命中总数时返回 []，剩余不足时原样返回剩余结果，不补
        空项。offset 为 0 时不跳过任何命中。

        根目录不存在或目录下尚无索引文件时返回 []，不创建目录或索引；
        根路径不是目录，或已有索引无法打开/完成查询时抛出 KBError，
        即使偏移量很大也不跳过这些检查。
        """
        if sort not in ("id", "relevance"):
            raise KBError(f"未知的排序方式: {sort}")
        if match not in ("contains", "exact"):
            raise KBError(f"未知的匹配方式: {match}")
        if self.root.exists() and not self.root.is_dir():
            raise KBError(f"知识库根路径不是目录: {self.root}")
        if not self.root.is_dir() or not self.db_path.exists():
            return []
        needle = query.casefold()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT v.doc_id, v.version, v.title"
                    " FROM versions AS v"
                    " JOIN ("
                    "     SELECT doc_id, MAX(version) AS latest_version"
                    "     FROM versions GROUP BY doc_id"
                    " ) AS latest"
                    " ON v.doc_id = latest.doc_id"
                    " AND v.version = latest.latest_version"
                    " ORDER BY v.doc_id ASC"
                ).fetchall()
        except sqlite3.Error as exc:
            raise KBError(f"索引无法打开或查询失败: {exc}") from exc

        def is_hit(title: str) -> bool:
            folded = title.casefold()
            if match == "exact":
                return folded == needle
            return needle in folded

        hits = [
            {"id": doc_id, "version": version, "title": title}
            for doc_id, version, title in rows
            if is_hit(title)
        ]
        if sort == "relevance" and match == "contains":
            def group(item: dict) -> int:
                folded = item["title"].casefold()
                if folded == needle:
                    return 0
                if folded.startswith(needle):
                    return 1
                return 2

            hits.sort(key=lambda item: (group(item), item["id"]))
        if offset:
            # 偏移量作用于最终排序结果，统计命中条数而非文档 ID
            hits = hits[offset:]
        if limit is not None:
            # 数量上限在偏移之后生效；剩余不足时切片原样返回全部
            hits = hits[:limit]
        return hits
