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
        """读取指定版本正文；version 为 None 时读取最新版本。"""
        self._require_initialized(doc_id)
        with self._connect() as conn:
            self._require_document(conn, doc_id)
            if version is None:
                row = conn.execute(
                    "SELECT body_path FROM versions WHERE doc_id = ?"
                    " ORDER BY version DESC LIMIT 1",
                    (doc_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT body_path FROM versions WHERE doc_id = ? AND version = ?",
                    (doc_id, version),
                ).fetchone()
        if row is None:
            raise KBError(f"版本不存在: 文档 {doc_id} 的版本 {version}")
        return (self.root / row[0]).read_bytes()

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
