"""SQLite 索引与正文文件的持久化层。

知识库目录结构::

    <root>/
        kb.sqlite3            # 文档与版本索引
        documents/<id>/v<n>.md  # 各版本正文原文（UTF-8）

正文按原始文本逐版本保存，SQLite 只保存文档 ID、版本号、当时的标题
以及正文文件路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_FILENAME = "kb.sqlite3"
DOCUMENTS_DIRNAME = "documents"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    current_version INTEGER NOT NULL CHECK (current_version >= 1)
);

CREATE TABLE IF NOT EXISTS versions (
    doc_id INTEGER NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    content_path TEXT NOT NULL,
    PRIMARY KEY (doc_id, version),
    FOREIGN KEY (doc_id) REFERENCES documents(id)
);
"""


class DocumentNotFoundError(LookupError):
    """文档 ID 在知识库中不存在。"""


class VersionNotFoundError(LookupError):
    """文档存在，但指定版本不存在。"""


def _content_path(root: Path, doc_id: int, version: int) -> Path:
    return root / DOCUMENTS_DIRNAME / str(doc_id) / f"v{version}.md"


def _connect(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / DB_FILENAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_document(root: Path, title: str, content: str) -> tuple[int, int]:
    """新增一篇文档，返回 (文档 ID, 版本号)，首次调用时自动初始化目录与索引。"""
    root.mkdir(parents=True, exist_ok=True)
    conn = _connect(root)
    try:
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute(
            "INSERT INTO documents (current_version) VALUES (1)"
        )
        doc_id = int(cur.lastrowid)
        content_path = _content_path(root, doc_id, 1)
        conn.execute(
            "INSERT INTO versions (doc_id, version, title, content_path)"
            " VALUES (?, 1, ?, ?)",
            (doc_id, title, str(content_path.relative_to(root))),
        )
        content_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" 避免任何换行符转换，按输入原样落盘。
        with open(content_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        conn.commit()
        return doc_id, 1
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_document(
    root: Path, doc_id: int, title: str, content: str
) -> tuple[int, int]:
    """为已有文档追加一个新版本，返回 (文档 ID, 新版本号)。"""
    conn = _open_existing(root)
    try:
        row = conn.execute(
            "SELECT current_version FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise DocumentNotFoundError(str(doc_id))
        next_version = int(row[0]) + 1
        content_path = _content_path(root, doc_id, next_version)
        conn.execute(
            "INSERT INTO versions (doc_id, version, title, content_path)"
            " VALUES (?, ?, ?, ?)",
            (doc_id, next_version, title,
             str(content_path.relative_to(root))),
        )
        content_path.parent.mkdir(parents=True, exist_ok=True)
        with open(content_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        conn.execute(
            "UPDATE documents SET current_version = ? WHERE id = ?",
            (next_version, doc_id),
        )
        conn.commit()
        return doc_id, next_version
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_content(root: Path, doc_id: int, version: int | None = None) -> str:
    """读取正文；version 为 None 时读取最新版本，按 UTF-8 原文返回。"""
    conn = _open_existing(root)
    try:
        row = conn.execute(
            "SELECT current_version FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise DocumentNotFoundError(str(doc_id))
        wanted = version if version is not None else int(row[0])
        vrow = conn.execute(
            "SELECT content_path FROM versions"
            " WHERE doc_id = ? AND version = ?",
            (doc_id, wanted),
        ).fetchone()
        if vrow is None:
            raise VersionNotFoundError(str(wanted))
        return (root / vrow[0]).read_bytes().decode("utf-8")
    finally:
        conn.close()


def list_history(root: Path, doc_id: int) -> list[tuple[int, str]]:
    """按版本号升序返回 (版本号, 当时标题) 列表。"""
    conn = _open_existing(root)
    try:
        doc = conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if doc is None:
            raise DocumentNotFoundError(str(doc_id))
        rows = conn.execute(
            "SELECT version, title FROM versions"
            " WHERE doc_id = ? ORDER BY version ASC",
            (doc_id,),
        ).fetchall()
        return [(int(v), t) for v, t in rows]
    finally:
        conn.close()


def _open_existing(root: Path) -> sqlite3.Connection:
    if not (root / DB_FILENAME).is_file():
        # 索引尚不存在，任何文档都不可能存在。
        raise DocumentNotFoundError("(知识库尚未初始化)")
    return _connect(root)
