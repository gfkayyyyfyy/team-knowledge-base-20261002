"""命令行入口：python -m knowledge_base --root <目录> <add|update|show|history> ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import storage
from .storage import DocumentNotFoundError, VersionNotFoundError


class CliError(Exception):
    """用户输入或数据状态错误，统一以退出码 2 报告。"""


def _positive_int(raw: str, what: str) -> int:
    text = str(raw).strip()
    if not text.isdigit() or int(text) <= 0:
        raise CliError(f"{what}必须是正整数: {raw!r}")
    return int(text)


def _validate_title(raw: str) -> str:
    title = raw.strip()
    if not title:
        raise CliError("标题去除首尾空白后不能为空")
    if "\n" in title or "\r" in title:
        raise CliError("标题必须为单行文本，不能包含换行")
    return title


def _read_body(raw_path: str) -> str:
    path = Path(raw_path)
    if not path.is_file():
        raise CliError(f"正文文件不存在或不是普通文件: {raw_path}")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise CliError(f"正文无法按 UTF-8 解码: {raw_path}")


def _cmd_add(args: argparse.Namespace) -> int:
    # 先完成全部校验，校验失败时不初始化目录、不新增文档。
    title = _validate_title(args.title)
    body = _read_body(args.file)
    doc_id, version = storage.add_document(Path(args.root), title, body)
    print(json.dumps(
        {"id": doc_id, "version": version, "title": title},
        ensure_ascii=False,
    ))
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    doc_id = _positive_int(args.id, "文档 ID")
    title = _validate_title(args.title)
    body = _read_body(args.file)
    try:
        _, version = storage.update_document(Path(args.root), doc_id, title, body)
    except DocumentNotFoundError:
        raise CliError(f"文档不存在: {doc_id}")
    print(json.dumps(
        {"id": doc_id, "version": version, "title": title},
        ensure_ascii=False,
    ))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    doc_id = _positive_int(args.id, "文档 ID")
    version = None
    if args.version is not None:
        version = _positive_int(args.version, "版本号")
    try:
        body = storage.get_content(Path(args.root), doc_id, version)
    except DocumentNotFoundError:
        raise CliError(f"文档不存在: {doc_id}")
    except VersionNotFoundError:
        raise CliError(f"版本不存在: 文档 {doc_id} 版本 {version}")
    # 直接写字节，保证正文原样输出，不追加标题或换行。
    sys.stdout.buffer.write(body.encode("utf-8"))
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    doc_id = _positive_int(args.id, "文档 ID")
    try:
        records = storage.list_history(Path(args.root), doc_id)
    except DocumentNotFoundError:
        raise CliError(f"文档不存在: {doc_id}")
    payload = [{"version": v, "title": t} for v, t in records]
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge_base",
        description="本地团队知识库：Markdown 文档的保存与修订读取",
    )
    parser.add_argument(
        "--root", required=True, help="知识库目录（首次成功 add 时自动初始化）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="新增一篇文档，生成版本 1")
    p_add.add_argument("--title", required=True, help="文档标题（非空单行）")
    p_add.add_argument("--file", required=True, help="正文文件路径（UTF-8）")
    p_add.set_defaults(func=_cmd_add)

    p_upd = sub.add_parser("update", help="为已有文档追加下一个连续版本")
    p_upd.add_argument("id", help="已有文档 ID（正整数）")
    p_upd.add_argument("--title", required=True, help="新版本的标题")
    p_upd.add_argument("--file", required=True, help="新版本正文文件路径")
    p_upd.set_defaults(func=_cmd_update)

    p_show = sub.add_parser("show", help="将正文原样输出到标准输出")
    p_show.add_argument("id", help="文档 ID（正整数）")
    p_show.add_argument(
        "--version", default=None, help="读取指定历史版本，默认为最新版本"
    )
    p_show.set_defaults(func=_cmd_show)

    p_hist = sub.add_parser("history", help="按版本号升序列出版本与标题")
    p_hist.add_argument("id", help="文档 ID（正整数）")
    p_hist.set_defaults(func=_cmd_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 保证 JSON 中的中文在任何 locale 下都能输出。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
