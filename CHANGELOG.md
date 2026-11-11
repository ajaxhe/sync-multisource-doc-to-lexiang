# Changelog

本 Skill 的变更记录。日期为实测 / 发布日，`实测` 标记的都是对着真实乐享 MCP 跑过的事实。

## 1.0.0 — 2026-11-11

首次开源。**主要变更是导入接口换形**：乐享把建任务与查状态合并成一个工具，脚本的调用层、
结果口径、命令语义随之重写。

### 接口换形（乐享侧，2026-11-11 实测）

- `import_create_task` + `import_describe_task` **合并为 `entry_import_connector`**。
  旧名直呼返回 `tool not found or not allowed`，两条调用路径（直调 / `call_tool` 包装）都不可用。
- 入参从 `{type, files}` 改成**顶层 oneof**，源类型就是字段名：

  ```json
  {"space_id": "…", "parent_entry_id": "…", "conflict_strategy": "skip",
   "txdoc": {"files": [{"id": "https://docs.qq.com/doc/…"}]}}
  ```

  写成旧形状不会报「字段不对」—— 未知字段被静默丢弃，只剩一句
  `source: exactly one field is required in oneof`，看不出所以然。
- 回包只有 `{"code":0,"data":{"task_id":…}}`：**没有状态查询、没有 `failed_items`、没有进度**。
  26 个候选查询名全部 `not found`。
- **`dry_run` 被忽略**：传了照样真写库。
- **提交阶段几乎不校验链接**：实测把一条 `/aio/` 坏链接和一条好链接放同一批，照样拿到 `task_id`，
  坏链接只表现为「目标目录没新增」。
- `entry_import_connector` **不在 `tools/list`（89 个工具里没有）但直调可用** ——
  此前「网关只放行 `tools/list` 里列出的工具」的结论已推翻，不要再用 tools/list 反推能力。

### 脚本（`scripts/sync.py`）

- 新增 `IMPORT_TOOL` / `LIST_TOOL` 常量；`import_args()` 负责把 config 的 `{type, files}` 展开成顶层 oneof。
- `submit()` 去掉 `dry_run` 与轮询，只判「有没有拿到 `task_id`」。
- **入库判据改为目录前后比对**：提交前 `entry_list_children` 快照 → 提交 → 轮询新增条目，
  连续 8 秒无新增即收尾。新增 `list_children()` / `wait_new_entries()` / `mark_reused()`。
- 拆单兜底的触发条件收窄为「调用本身报错」（原来靠 `failed_items` 判断，该字段已不存在）。
- `status` 从「查任务状态」改为「拿上次提交前的快照当基线，列出目录新增了什么」。
- `--wait` 默认值 900 → 180 秒（现在只是等目录出现条目，不需要那么久）。

### 结果口径（有破坏性变更）

旧的四档 `ok / reused / failed / ignored`（外加 `pending`）改为：

| 字段 | 含义 |
|---|---|
| `ok` | 目标目录实际新增的条目数（唯一硬指标） |
| `reused` | 提交链接的 `name` 早已存在于目录（**仅 config 写了 `name` 才判得出**） |
| `unknown` | 提交成功但目录没新增 —— 可能是 `skip` 去重，也可能压根没入库，**脚本不猜** |
| `failed` | 提交阶段就失败（没拿到 `task_id`） |

`unknown` 是一次刻意的能力让步：接口不再给条目级结果，硬猜就会把「没入库」报成「已存在」，
那是最坑的假成功。退出码：有提交失败、或「一条都没新增」→ 1。

### 修复

- **轮询会把整轮 run 拖死**：原实现拿「新增集合非空」当变化，计时器每轮刷新，目录稳定后仍一直等到
  `--wait` 上限（实测首跑被外层 120s 超时掐死，exit 137）。改成「只有又多了才算变化」，补离线用例 D9。
- **`--debug` 曾是空开关**：`Log` 建了但没传给 `api.Client`，`sync.log` 一直为空。
- **`--dry-run` 不再假装预演**：新接口忽略 `dry_run`，留一个「看起来没写库其实写了」的开关比没有更危险，
  现在直接拒绝执行（退 2）并指向离线的 `plan`。
- **工具被拒的第三种文案**：`tool not found or not allowed:` 未被识别，纯文本被当正常返回值上传，
  上层拿 `str` 当 `dict` 用直接 `AttributeError` 崩在半路。

### 文档与自检

- `SKILL.md`：描述、命令表、结果口径、排错表、测试清单全部按新接口重写。
- `references/import-api.md`：新增 §0「现行接口」全量语义；旧接口字段表标为历史；§5 改成三次修正的
  调用路径对照，并更正「只放行 tools/list」的错误归纳。
- `references/test-links.md`：腾讯文档 T1–T4 标记为已入库，重写判读标准（旧版「12 进 / 6 失败 / 1 忽略」
  是 `import_describe_task` 时代的口径）。
- `scripts/selftest.py`：31 个离线用例全绿。新增 D8（oneof 入参形状）、D9（轮询收尾）、
  E5（`--dry-run` 必须被拒绝），D1–D5 按新口径重写。

### 实测

- 腾讯文档个人版 4 条链接（3 篇 `/doc/` + 1 个 `/sheet/`）：一次提交，**24 秒内 4 条全部落库**，
  `source.platform = txdoc`、`entry_type = file`，sheet 落成 `.xls`。
- 同批再跑一次：`skip` 生效，目录零新增（`ok=0 / unknown=4`，退出码 1，提示先 `status` 核对）。
- `/aio/` 智能文档与腾讯文档企业版 `saas.docs.qq.com` 仍不支持。

## 0.x — 2026-10（内部迭代，未公开发布）

- 支持企业微信 / 飞书 / 钉钉 / 腾讯文档个人版四源导入，一个源一个 task。
- 链接清单托管在乐享文档时，`init --from` / `collect` 自动抽链接、按源分组。
- 任务级失败自动拆单重试；`conflict_strategy` 三档（`skip` / `replace` / `keep_both`）。
