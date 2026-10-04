"""命令行入口：python -m knowledge_base --root DIR <add|update|show|history|search|diff>。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .store import KBError, Store, read_body_file


def positive_int(value: str) -> int:
    """argparse 类型：正整数。"""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"不是有效的整数: {value}")
    if number < 1:
        raise argparse.ArgumentTypeError(f"必须是正整数: {value}")
    return number


def limit_arg(value: str) -> int:
    """argparse 类型：--limit 的数量参数，仅接受严格的正整数。

    与 int(value) 不同，这里只接受 ASCII 十进制数字组成的非空串，
    不接受带空白、正负号、小数点或下划线的写法（如 " 2"、"+2"、
    "2.0"、"1_0"），也不接受上标等非 ASCII 数字字符；空值、零与
    负数同样拒绝，使数量参数的合法形式唯一确定。
    """
    if not value or not all("0" <= ch <= "9" for ch in value):
        raise argparse.ArgumentTypeError(
            f"数量参数必须是正整数: {value!r}"
        )
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(
            f"数量参数必须是正整数: {value!r}"
        )
    return number


def offset_arg(value: str) -> int:
    """argparse 类型：--offset 的偏移量参数，仅接受非负整数的严格写法。

    与 limit_arg 同样只接受 ASCII 十进制数字组成的非空串，不接受带空白、
    正负号、小数点或下划线的写法，也不接受非 ASCII 数字字符；与 --limit
    不同的是允许 0（含前导零，如 "0"、"007"），省略时等同于 0。
    """
    if not value or not all("0" <= ch <= "9" for ch in value):
        raise argparse.ArgumentTypeError(
            f"偏移量参数必须是非负整数: {value!r}"
        )
    return int(value)


def title_arg(value: str) -> str:
    """argparse 类型：去除首尾空白后非空的单行标题。"""
    title = value.strip()
    if not title:
        raise argparse.ArgumentTypeError("标题去除首尾空白后不能为空")
    if "\n" in title or "\r" in title:
        raise argparse.ArgumentTypeError("标题必须为单行文本")
    return title


def search_query_arg(value: str) -> str:
    """argparse 类型：去除首尾空白后非空的查询词，内部空白原样保留。"""
    query = value.strip()
    if not query:
        raise argparse.ArgumentTypeError("QUERY 去除首尾空白后不能为空")
    return query


def output_path_arg(value: str) -> str:
    """argparse 类型：--output 的目标路径，仅拒绝空字符串。

    不做空白裁剪，路径内部及首尾的空白都是路径的一部分；也不规范化、
    不解析符号链接或要求此刻存在。相对路径按调用时工作目录解释的工作
    交给 pathlib 与写入时的 open 完成。
    """
    if value == "":
        raise argparse.ArgumentTypeError("--output 目标路径不能为空字符串")
    return value


def export_body(body: bytes, output_arg: str) -> None:
    """把所选版本正文逐字节导出到 output_arg 指定的本地文件。

    相对路径按调用时工作目录解释（交给 Path/open，不做 chdir 或基于
    --root 的重解释），接受绝对路径与含空格的路径，不强制扩展名。
    目标父目录必须已经存在且为目录；目标路径本身（文件、目录或其他
    任何已存在条目）一律拒绝覆盖。写入用 O_CREAT|O_EXCL 独占创建，
    因此竞态情况下也不会截断已有文件；空正文生成零字节文件。

    写入失败时删除本次新建的半截文件（确认仍为本次创建的普通文件后），
    不留残余。任何失败都以 KBError 报告，由命令行统一转为退出码 2。
    """
    target = Path(output_arg)
    parent = target.parent
    # Path("name").parent 为 Path(".")，指向当前工作目录；始终需要检查
    if not parent.is_dir():
        raise KBError(f"导出目标父目录不存在或不是目录: {parent}")
    # 在独占创建前先给出明确的“已存在”错误（含目录等一切条目类型）
    if target.exists() or target.is_symlink():
        raise KBError(f"导出目标已存在，拒绝覆盖: {target}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(target, flags, 0o666)
    except FileExistsError:
        raise KBError(f"导出目标已存在，拒绝覆盖: {target}")
    except OSError as exc:
        raise KBError(f"导出目标无法写入: {target}: {exc}")
    try:
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    except OSError as exc:
        os.close(fd)
        # 仅清理本次新建且仍为普通文件的目标，避免误删竞态中出现的其他条目
        try:
            if target.is_file() and not target.is_symlink():
                target.unlink()
        except OSError:
            pass
        raise KBError(f"导出目标无法写入: {target}: {exc}")
    else:
        os.close(fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge_base",
        description="本地团队知识库：Markdown 文档的保存与修订读取",
    )
    parser.add_argument("--root", required=True, help="知识库根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="新增文档，生成版本 1")
    p_add.add_argument("--title", required=True, type=title_arg, help="文档标题")
    p_add.add_argument("--file", required=True, help="正文 Markdown 文件路径")

    p_update = sub.add_parser("update", help="更新文档，生成下一个连续版本")
    p_update.add_argument("id", type=positive_int, help="文档 ID")
    p_update.add_argument("--title", required=True, type=title_arg, help="文档标题")
    p_update.add_argument("--file", required=True, help="正文 Markdown 文件路径")

    p_show = sub.add_parser("show", help="输出文档正文，默认最新版本")
    p_show.add_argument("id", type=positive_int, help="文档 ID")
    p_show.add_argument("--version", type=positive_int, default=None, help="版本号")
    p_show.add_argument(
        "--output",
        type=output_path_arg,
        default=None,
        metavar="FILE",
        help="把所选版本正文逐字节导出为本地文件；省略时原样写入标准输出",
    )

    p_history = sub.add_parser("history", help="列出文档全部版本")
    p_history.add_argument("id", type=positive_int, help="文档 ID")

    p_diff = sub.add_parser("diff", help="对比同一文档两个版本的正文差异")
    p_diff.add_argument("id", type=positive_int, help="文档 ID")
    p_diff.add_argument(
        "--from",
        dest="from_version",
        type=positive_int,
        required=True,
        help="差异旧侧版本号（正整数）",
    )
    p_diff.add_argument(
        "--to",
        dest="to_version",
        type=positive_int,
        required=True,
        help="差异新侧版本号（正整数）",
    )

    p_search = sub.add_parser("search", help="按最新标题字面子串检索文档")
    p_search.add_argument("query", metavar="QUERY", type=search_query_arg, help="标题查询词")
    p_search.add_argument(
        "--sort",
        choices=["id", "relevance"],
        default="id",
        help="结果排序：id 按文档 ID 升序（默认）；relevance 按标题匹配程度分组",
    )
    p_search.add_argument(
        "--limit",
        type=limit_arg,
        default=None,
        metavar="N",
        help="只返回当前排序下最前面的 N 条结果，N 为正整数；省略时返回全部命中",
    )
    p_search.add_argument(
        "--offset",
        type=offset_arg,
        default=0,
        metavar="M",
        help="跳过最终排序结果中的前 M 条命中，M 为非负整数；省略时等于 0",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.root)
    try:
        if args.command == "add":
            data = read_body_file(args.file)
            doc_id, version = store.add(args.title, data)
            result = {"id": doc_id, "version": version, "title": args.title}
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "update":
            data = read_body_file(args.file)
            version = store.update(args.id, args.title, data)
            result = {"id": args.id, "version": version, "title": args.title}
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "show":
            if args.output is not None:
                # 导出入口对根路径给出明确原因（缺失/不是目录），不含糊报为文档不存在；
                # 省略 --output 的既有报错文本保持不变
                if not store.root.exists():
                    raise KBError(f"知识库根目录不存在: {store.root}")
                if not store.root.is_dir():
                    raise KBError(f"知识库根路径不是目录: {store.root}")
                if not store.db_path.is_file():
                    raise KBError(f"索引缺失或无法查询: {store.db_path}")
            body = store.get_body(args.id, args.version)
            if args.output is None:
                # 原样输出正文，不额外添加标题或换行
                sys.stdout.buffer.write(body)
            else:
                # 先成功读取所选版本正文，再执行导出；失败不产生文件
                export_body(body, args.output)
        elif args.command == "history":
            print(json.dumps(store.history(args.id), ensure_ascii=False))
        elif args.command == "diff":
            text = store.diff(args.id, args.from_version, args.to_version)
            # 正文相同时 text 为空串，标准输出保持为空，不输出文件头
            if text:
                sys.stdout.buffer.write(text.encode("utf-8"))
        elif args.command == "search":
            print(
                json.dumps(
                    store.search(
                        args.query,
                        sort=args.sort,
                        limit=args.limit,
                        offset=args.offset,
                    ),
                    ensure_ascii=False,
                )
            )
    except KBError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
