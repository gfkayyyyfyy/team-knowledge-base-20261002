"""存储层：正文以 UTF-8 文本文件保存，SQLite 保存文档与版本索引。"""

from __future__ import annotations

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
        """更新已有文档，生成下一个连续版本，返回新版本号。"""
        self._require_initialized(doc_id)
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
        return version

    def get_body(self, doc_id: int, version: int | None = None) -> bytes:
        """读取指定版本正文；version 为 None 时读取最新版本。

        所选正文文件缺失、不是普通文件或无法按 UTF-8 解码时抛出 KBError，
        不回退到其他版本。
        """
        self._require_initialized(doc_id)
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
        if row is None:
            raise KBError(f"版本不存在: 文档 {doc_id} 的版本 {version}")
        selected_version, rel = row
        path = self.root / rel
        if not path.is_file():
            raise KBError(
                f"正文文件缺失或不是普通文件: 文档 {doc_id} 的版本"
                f" {selected_version} ({rel})"
            )
        data = path.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise KBError(
                f"正文无法按 UTF-8 解码: 文档 {doc_id} 的版本"
                f" {selected_version} ({rel})"
            )
        return data

    def history(self, doc_id: int) -> list[dict]:
        """按版本号升序返回 [{version, title}, ...]，不产生修订。"""
        self._require_initialized(doc_id)
        with self._connect() as conn:
            self._require_document(conn, doc_id)
            rows = conn.execute(
                "SELECT version, title FROM versions WHERE doc_id = ?"
                " ORDER BY version ASC",
                (doc_id,),
            ).fetchall()
        return [{"version": version, "title": title} for version, title in rows]

    def search(
        self, query: str, sort: str = "id", limit: int | None = None
    ) -> list[dict]:
        """按每篇文档的最新标题做忽略大小写的字面子串匹配。

        匹配在 Python 端用 str.casefold 完成，%、_、*、[、] 等均为普通
        字符，不读取正文与历史标题。返回 [{id, version, title}, ...]，
        标题保留原文，不产生任何修订或文件。

        sort 为 "id"（默认）时按文档 ID 升序；为 "relevance" 时按标题
        匹配程度分组：完全相等的最前，以查询词开头的其次，其余包含
        查询词的最后，同组内按文档 ID 升序。分组沿用与匹配相同的
        casefold 语义，不按出现次数、标题长度或版本号再排序。

        limit 为正整数时只返回最终排序结果的前 limit 条，与不设上限的
        同一查询前 limit 条完全一致；命中不足 limit 条时返回全部命中，
        不补空项。limit 为 None 时返回全部命中。

        根目录不存在或目录下尚无索引文件时返回 []，不创建目录或索引；
        根路径不是目录，或已有索引无法打开/完成查询时抛出 KBError。
        """
        if sort not in ("id", "relevance"):
            raise KBError(f"未知的排序方式: {sort}")
        if limit is not None and limit < 1:
            raise KBError(f"数量上限必须是正整数: {limit}")
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
        hits = [
            {"id": doc_id, "version": version, "title": title}
            for doc_id, version, title in rows
            if needle in title.casefold()
        ]
        if sort == "relevance":
            def group(item: dict) -> int:
                folded = item["title"].casefold()
                if folded == needle:
                    return 0
                if folded.startswith(needle):
                    return 1
                return 2

            hits.sort(key=lambda item: (group(item), item["id"]))
        if limit is not None:
            hits = hits[:limit]
        return hits
