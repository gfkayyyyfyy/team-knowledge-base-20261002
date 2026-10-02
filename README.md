# 本地团队知识库

建设用于团队操作指南和项目笔记的本地知识管理产品，逐步覆盖 Markdown 条目、目录与标签、修订历史、关键词检索、内部链接、归档和导出。

计划采用：Python 3 标准库 / sqlite3 / pathlib / argparse。

## 当前功能：单篇 Markdown 文档的本地保存与修订读取

仅依赖 Python 3 标准库。统一入口：

```
python -m knowledge_base --root <知识库目录> <命令> [参数]
```

首次成功 `add` 时自动初始化 `<知识库目录>`：

- `kb.sqlite3` —— 文档与版本索引（SQLite）
- `documents/<id>/v<n>.md` —— 各版本正文原文（UTF-8，逐版本保存，不解析、不改写 Markdown）

后续独立启动的命令访问同一份数据。

### 命令

| 命令 | 说明 |
| --- | --- |
| `add --title <标题> --file <正文文件>` | 新增文档，分配知识库内唯一正整数 ID，生成版本 1。成功后输出 `{"id", "version", "title"}` JSON |
| `update <id> --title <标题> --file <正文文件>` | 保留 ID，追加下一个连续版本；即使内容与上一版相同也保留修订。输出同样的 JSON |
| `show <id> [--version <n>]` | 默认将最新正文原样写入标准输出（不额外添加标题或换行）；`--version` 读取历史正文 |
| `history <id>` | 按版本号升序输出 JSON 数组，每项为 `{"version", "title"}`；查询不产生修订 |

标题去除首尾空白后必须为非空单行文本；正文允许为空，中文、空行及末尾换行按输入原样保留。每个历史版本同时保存当时的标题和正文，改标题不影响旧版本读取。

### 退出约定

- 成功：退出码 `0`。
- 标题不合法、ID 或版本号不是正整数、文档或版本不存在、正文文件不存在或不是普通文件、正文无法按 UTF-8 解码：退出码 `2`，原因写入标准错误，标准输出为空，且不新增文档或版本、已有数据不变。

### 示例

```bash
printf '# 指南\n\n正文中文。\n' > v1.md
python -m knowledge_base --root ./kb add --title '操作指南' --file v1.md
# {"id": 1, "version": 1, "title": "操作指南"}

python -m knowledge_base --root ./kb show 1        # 输出最新正文原文
python -m knowledge_base --root ./kb history 1     # [{"version": 1, "title": "操作指南"}]

printf '# 指南 v2\n\n修订后的正文。\n' > v2.md
python -m knowledge_base --root ./kb update 1 --title '操作指南（修订）' --file v2.md
# {"id": 1, "version": 2, "title": "操作指南（修订）"}

python -m knowledge_base --root ./kb show 1 --version 1   # 仍输出版本 1 原文
```

目录与标签、检索、内部链接、归档、导出和浏览页面留待后续迭代。
