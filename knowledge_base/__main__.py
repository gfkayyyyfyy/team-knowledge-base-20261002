"""命令行入口：python -m knowledge_base --root DIR <add|update|show|history|search|diff>。"""

from __future__ import annotations

import argparse
import json
import sys

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


def output_arg(value: str) -> str:
    """argparse 类型：--output 的导出路径，拒绝空字符串，其余原样保留。"""
    if not value:
        raise argparse.ArgumentTypeError("导出路径不能为空字符串")
    return value


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
        type=output_arg,
        default=None,
        metavar="FILE",
        help="把所选版本正文逐字节导出到本地文件；目标必须不存在且父目录已存在。"
        "省略时正文仍写入标准输出",
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
        "--match",
        choices=["contains", "exact"],
        default="contains",
        help="匹配方式：contains 标题字面子串包含查询词即命中（默认）；"
        "exact 仅在最新标题与查询词完整相等时命中，前缀与中间包含均不算",
    )
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
            if args.output is None:
                body = store.get_body(args.id, args.version)
                # 原样输出正文，不额外添加标题或换行
                sys.stdout.buffer.write(body)
            else:
                # 导出成功时标准输出与标准错误均为空
                store.export_body(args.id, args.version, args.output)
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
                        match=args.match,
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
