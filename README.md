# 本地团队知识库

建设用于团队操作指南和项目笔记的本地知识管理产品，逐步覆盖 Markdown 条目、目录与标签、修订历史、关键词检索、内部链接、归档和导出。

计划采用：Python 3 标准库 / sqlite3 / pathlib / argparse。

## 当前功能

单篇 Markdown 文档的本地保存与修订读取。正文以 UTF-8 文本原样保存在知识库目录（`bodies/`），SQLite（`knowledge_base.sqlite3`）保存文档与版本索引。首次成功新增时自动初始化目录与索引。

统一入口为 `python -m knowledge_base`，通过 `--root` 指定知识库目录：

```bash
# 新增文档，分配唯一正整数 ID，生成版本 1
python -m knowledge_base --root KB_DIR add --title '标题' --file body.md

# 更新文档，保留 ID，生成下一个连续版本（内容相同也保留本次修订）
python -m knowledge_base --root KB_DIR update ID --title '新标题' --file body.md

# 输出最新正文（原样写入标准输出）；--version 读取历史版本
python -m knowledge_base --root KB_DIR show ID [--version N]

# 按版本号升序列出全部版本：[{"version": 1, "title": "..."}, ...]
python -m knowledge_base --root KB_DIR history ID

# 按最新标题做忽略大小写的字面子串检索：[{"id": ..., "version": ..., "title": "..."}, ...]
python -m knowledge_base --root KB_DIR search QUERY [--sort id|relevance] [--limit N] [--offset M]
```

- add / update 成功后，标准输出返回 `{"id": ..., "version": ..., "title": "..."}`。
- search 仅匹配每篇文档的最新标题（不搜正文与历史标题），QUERY 去除首尾空白后匹配，内部空格原样保留，按 Unicode casefold 语义忽略大小写；`%`、`_`、`*`、`[`、`]` 等均为普通字符，每篇至多一条。`--sort` 仅接受 `id` 与 `relevance`，省略或取 `id` 时按文档 ID 升序；取 `relevance` 时完全相等的标题最前、以查询词开头的其次、其余包含查询词的最后，同组内按文档 ID 升序，分组沿用同一 casefold 语义。`--limit N` 可选，N 仅接受正整数，在最终排序完成后只保留最前面的 N 条（即与不设上限的同一次查询前 N 条完全一致），命中少于 N 条时返回全部命中，不补空项，也不附加总数或截断提示；省略时返回全部命中。`--offset M` 可选，M 仅接受非负整数（允许 0 与前导零），在最终排序完成、应用 `--limit` 之前先跳过前 M 条命中（偏移量统计的是命中条数，不是文档 ID）；偏移量等于或超过命中总数时输出 `[]`，剩余不足时原样返回剩余结果，不补空项；省略时等于 0。根目录不存在、目录尚无索引或无命中时输出 `[]`（退出码 0），不创建目录或索引；QUERY 缺失/空白、`--sort` 缺值或取值非法、`--limit` 缺值或取值不是正整数（空值、零、负数、小数、非整数字符串等）、`--offset` 缺值或取值不是非负整数（空值、负数、带正号、带空白、小数、下划线、非 ASCII 数字等）、根路径不是目录或索引无法查询时以退出码 2 报错，标准输出为空；`--limit` 或 `--offset` 取值非法时即使根目录不存在也按参数错误处理，不输出空数组。
- 标题去除首尾空白后应为非空单行文本；正文允许为空，中文、空行及末尾换行按输入保留，不解析或改写 Markdown。
- 历史版本同时保留当时的标题和正文，标题变更不影响旧版本读取。
- 参数非法、文档或版本不存在、正文文件缺失/非普通文件/非 UTF-8 时，命令以退出码 2 结束，原因写入标准错误，标准输出为空，已有数据不变；有效命令以退出码 0 结束。

目录与标签、检索、内部链接、归档、导出和浏览页面留待后续。
