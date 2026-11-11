# 乐享导入接口（脚本依赖的事实）

> 🔴 **2026-11-11 接口换形**：`import_create_task` + `import_describe_task` **合并成 `entry_import_connector`**。
> 建任务还是建任务，但**没有状态查询、没有条目级明细、没有 dry_run**（传了也真写库）。
> 本文 §1–§2 保留旧接口字段表作历史对照，现行用法见 §0。

## 0. 现行接口 `entry_import_connector`

### 入参：顶层 oneof，源类型就是字段名

```json
{
  "space_id": "<32 位知识库 id>",
  "parent_entry_id": "<32 位目标目录 id>",
  "conflict_strategy": "skip",
  "txdoc": {"files": [{"id": "https://docs.qq.com/doc/DU0ZBTFdGc2RmYUNq"}]}
}
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `space_id` | ✅ | 目标知识库 |
| `parent_entry_id` | ✅ | 目标目录；知识库根节点也可以 |
| `<源类型>` | ✅ | **顶层 oneof 成员**，名字就是源类型（`txdoc` / `wecombot` / `feishu` / `dingtalk` …），值 `{"files": […]}` |
| `files[].id` | ✅ | 链接原文 |
| `files[].name` / `include_subpages` | — | 选填，字段名与旧接口一致（`name` 现在还有额外用处：脚本靠它判「已存在被 skip」，见下） |
| `conflict_strategy` | — | `skip` / `replace` / `keep_both`。不传 = `replace` 语义 |

⚠️ **写成旧的 `{"type":"txdoc","files":[…]}` 不会提示「字段不对」** —— 未知字段被静默丢弃，只剩一句：

```
validate proto message: validation error: - source: exactly one field is required in oneof [required]
```

看不出所以然，只能靠记住这个形状。

### 回包

```json
{"code": 0, "message": "success", "data": {"task_id": "01a1c9dcb66f7f24b8767758e9b46354"}}
```

`code:0` 且拿到 `task_id` = **已受理**。到这儿就没有下文了 —— 没有 status、没有进度、没有明细。

### 三个必须记住的语义（2026-11-11 实测）

| 事实 | 证据 / 后果 |
|---|---|
| **没有状态查询** | 26 个候选名（`import_describe_task` / `entry_describe_import_task` / `entry_get_import_task` …）全部 `not found`。判入库只能**比对目标目录**（`entry_list_children` 前后 diff） |
| **`dry_run` 被忽略** | 传 `dry_run: true` 照样真写库。所以脚本 `run --dry-run` 直接拒绝执行（退 2），预览走离线的 `plan` |
| **`conflict_strategy: skip` 生效** | 同一批 4 条重复提交，目录零新增（返回新 `task_id`，但没建条目）。不传的行为未验，脚本永远显式下发 |
| **提交阶段几乎不校验链接** | 实测把 `[好链接, /aio/ 坏链接]` 放一批提交，**照样拿到 `task_id`**，不报错；坏链接只是「目录没新增」。所以逐条拆分**不再能**靠提交失败定位坏链接 |

⚠️ 最后一条最反直觉：旧接口靠 `failed_items` 告诉你哪条坏了，新接口把这个能力连同状态查询一起砍了。
**「提交成功」= 只是受理，连「链接是否合法」都没验证。** 目前唯一能定位单条坏链接的办法是
「一条一个 task 分别提交 + 看目录新增」，成本高，脚本默认不做。

### 入库判据

```python
before = list_children(client, parent_id)          # 提交前快照
submit(...)                                        # 一次 entry_import_connector
after  = list_children(client, parent_id)          # 轮询到连续 8 秒无新增
added  = {k: v for k, v in after.items() if k not in before}
```

实测：4 条链接（3 doc + 1 sheet）→ 24 秒内 4 条全部落库，条目 `source.platform = txdoc`、
`entry_type = file`、`target_type = kb_file`，sheet 落成 `.xls`。

⚠️ **新增条数 ≠ 提交条数**：文件夹型源会展开成多条，1 条链接可能落 3 个条目。所以脚本按**条目**计。

⚠️ **轮询要按「又多了」判变化**：拿「新增集合非空」当变化会让计时器每轮刷新，目录稳定后仍一直等到
`--wait` 上限（实测把整轮 run 拖到 2 分钟以上、被外层超时掐死）。

## 1. `import_create_task`（旧，已下线，仅作字段对照）

| 参数 | 必填 | 说明 |
|---|---|---|
| `space_id` | ✅ | 目标知识库 |
| `parent_entry_id` | ✅ | 目标目录。知识库根节点也能当父目录（实测可） |
| `type` | ✅ | 源类型，**任务级参数**，一次请求只能一个源 |
| `files[]` | ✅ | 元素：`id`（必填，链接原文）/ `name`（选填）/ `include_subpages`（选填） |
| `dry_run` | — | `true` 只预演，不写库 —— **新接口已无此语义** |
| `conflict_strategy` | — | `skip` / `replace` / `keep_both`。**不传 = `replace` 语义**。服务端校验器原文只有这三个值（2026-10-22 实测传 `overwrite` 被拒：`value must be in list ["replace","skip","keep_both"]`） |

### `replace` 的实测语义（2026-10-22，微盘链接场景）

- **同源命中**（source.href.id 字节一致）：保留原 `entry_id`，计入 `special_num`（脚本报「已存在」）；
- **不删异源身份的既有条目**：目录里手动/其它管道导入的条目（`fi…` 文件 id 身份）在两次
  `replace` 真跑后依然存在 —— ⚠️ 与前身 skill（sync-wecom-doc-to-lexiang）文档里
  「不在集合内的既有条目会被删掉」的描述**不符**，以本实测为准；不同源类型/时期的行为待复核；
- 「覆盖更新」生效的前提是**身份匹配**：想覆盖既有条目，必须用与当初导入时相同的链接/管道提交。
  换身份（如手动微盘导入 → drive 分享链接）提交 replace，只会**新增**，不会覆盖旧的。
- 盲试过的参数名（顶层 `overwrite` / `update_on_conflict` / `force_update`，files 元素级
  `overwrite` / `force` / `update` / `replace` / `refresh`）全部被服务端**静默忽略**，无行为差异。

返回 `data.task_id`。`code:0` 只代表任务创建成功，成败要轮询 `import_describe_task`。
字段名写错不会报错 —— 服务端静默忽略未知字段。

## 2. `import_describe_task`（旧，已下线）

> 2026-11-11 起查不到任务了。下表是**历史事实**，只在读老 state.json / 老日志时有用；
> 现行脚本不再调用它，改用 `entry_list_children` 比对目录。

入参 `task_id`。关键字段：

| 字段 | 说明 |
|---|---|
| `status` | `processing` / `page_processing` = 进行中；`succeed` / `failed` / `canceled` = 终态 |
| `total_num` / `current_num` / `percentage` | 进度 |
| `entries[]` | 真跑新增成功的条目；**命中既有条目时没有这个字段** |
| `failed_items[]` | 条目级失败：`id` / `name` / `failed_code` / `failed_reason` |
| `err_message` | 任务级错误，真正原因常在长串末尾 |
| `dry_run_stats` | **仅 `dry_run=true` 时回**：`add_num` / `special_num` |

### 五种响应形态（判状态必看）

| 场景 | 字段特征 |
|---|---|
| 真跑·新增 | 有 `entries`，`percentage=100`，`current_num=N` |
| 真跑·命中既有 | **无 `entries`**，有 `percentage` + `current_num` |
| 真跑·被过滤 | **只有** `status` + `total_num` |
| 预演·正常 | `dry_run_stats.add_num` 有值 |
| 预演·全被过滤 | `dry_run_stats = {}` |

### 任务级失败 vs 条目级失败

| | 条目级 | 任务级 |
|---|---|---|
| `status` | `failed` | `failed` / `canceled` |
| `failed_items` | 有明细 | **为空** |
| 原因在哪 | `failed_items[].failed_reason` | `err_message` |
| 典型场景 | 单篇文档打不开 / 权限不足 | 连接器未授权、连通失败、整批拒绝 |
| 计口 | 逐条算 | 全部候选算未完成 |

🔴 实测（2026-10-20）：任务级失败**不隔离** —— 8 条企微链接一个任务，1 条
「请你先打开文档」的 smartpage 让整个任务 failed，另外 7 条好链接也全算失败。
**隔离失败面的唯一手段是把链接拆进多个任务**（同 type 多次 `entry_import_connector`，
skip 策略下安全）。

新接口时代这条更硬：连「哪条坏了」的明细都没有了，只有整批失败/成功。所以脚本的拆单兜底从
「看 `failed_items` 决定要不要拆」变成「**提交失败就逐条重提**」，靠单条提交的返回值定位坏链接。

## 3. `type` 取值

| `type` | 源 |
|---|---|
| `wecombot` | 企业微信（文档 / 智能文档 / 表格 / 脑图） |
| `feishu` | 飞书 |
| `dingtalk` | 钉钉 |
| `txdoc` | 腾讯文档个人版（`docs.qq.com`）—— **2026-10-27 实测已可用**，见下节 |
| `iwiki` / `wps` / `html` / `markdown` / `confluence` | 其它，可用但脚本不做链接识别，候选上得显式写 `type` |

非法值回 `{"code": 50090008, "message": "导入类型不支持"}`，**不吐合法取值清单**。
所以试类型只能一个个试：`txdoc` 是**唯一**接受 腾讯文档 链接的取值，
`tencentdocs` / `tencent_doc` / `tdocs` / `qqdocs` / `docs` / `txdocs` 全部回 50090008。

### 腾讯文档个人版（`type=txdoc` / oneof `txdoc`）

| 结论 | 证据 |
|---|---|
| 取值 `txdoc` 被接口接受 | 2026-10-27 起不再回 50090008；11 个候选名里只有它可用 |
| `/doc/<id>`、`/sheet/<id>` 能导入 | **2026-11-11 实测 4/4 全部落库**（3 doc + 1 sheet，24 秒，`platform=txdoc`） |
| `/aio/<id>`（智能文档）不被接受 | `source selections are not importable`，与飞书老版 docs 同一类 |
| 授权是**前置人工步骤** | 2026-10-27 三个租户全部回授权失效；11-11 已授权，导入通过 |

任务级错误的两种原文（旧接口在 `err_message`，新接口在 `error` / 回包文本里）：

- csig 租户：`腾讯文档授权已失效，请重新授权`
- 另两个租户（带 workflow 栈）：`activity error (type: ExecuteNextBatch, …): list child nodes:
  腾讯文档导入失败，请稍后重试 (type: wrapError, retryable: true): 腾讯文档导入失败，请稍后重试
  (type: ReAuthError, retryable: true)`

⚠️ **`ReAuthError` 不是「腾讯文档授权失效」的同义词**：企微单篇文档没权限也报 `ReAuthError`
（见 test-links.md 第 7 条）。脚本的 `hints_of()` 只在原文含「腾讯文档授权」时提示去重新授权，
不按裸 `ReAuthError` 提示，避免把人引到错误的修法上。

**腾讯文档企业版（`saas.docs.qq.com`）不支持**（2026-10-27 产品侧确认），本轮不考虑。
脚本做了两层防护，避免拿必败的链接去提交：

- `guess_type()` 先判类型的 `exclude`，`saas.docs.qq.com` 不会被归成 `txdoc`；
- `unsupported_reason()` 给一句人话，`collect` 把它归进「认不出源类型」并注明原因，`plan` 逐条提示。

⚠️ 别自己把企业版链接手塞进 `type=txdoc` 的 task —— 校验层只挡「自动归类」，挡不住「你硬填」。

### 一个任务只能一个源（实测 2026-10-20）

`type` 是任务级参数，决定服务端用哪个源连接器去抓 `files[]` 的内容：

- `type=feishu` 的任务里放一条企微链接 → 任务级失败 `SelectionNotImportable`，整批拒绝；
- `type=wecombot` 的任务里放一条**以前以 feishu 类型导入过**的飞书链接 → 命中去重索引
  （`special_num`），看似"成功"实则没有新建，**不能**当成"混源可导入"的证据。

结论：同源多条 = 批量；跨源 = 拆多个任务。

## 4. 脚本依赖的其它接口行为

| 接口 / 场景 | 事实 |
|---|---|
| 去重 | 服务端按 `source.href.id` **字节比对**。所以候选必须回填服务端存的原始串 |
| `entry_list_children` | 参数名 `parent_id`（不是 `entry_id`），另有 `limit` / `page_token`。分页终止条件是**本页 `entries` 为空**，不是 `next_page_token` 为空 —— 实测空页照样回一个（等于上一页的）`next_page_token`，只看 token 会死循环 |
| `block_fetch_page` | 返回**纯文本**（markdown），不是 JSON |
| 内容更新检测 | **接口没有这个维度**。`skip` 档下源端内容变更不会同步；`special_num` 只表示「命中既有条目」 |
| 去重索引延迟 | 新建条目要过几分钟才进索引 |
| 知识库根节点 | 可作 `parent_entry_id`（实测 `dry_run` 回 `add_num`） |
| 微盘分享链接 | `https://drive.weixin.qq.com/s?k=…` 用 `type=wecombot` 可导入（2026-10-22 实测 4/4 成功） |
| endpoint 多租户 | 可带 `company_from` 参数：`https://mcp.lexiang-app.com/mcp?company_from=<id>&preset=all`，配对应租户的 token |
| 删除条目 | **MCP 无删除知识条目的工具**（knowledge.entry 只有 create/describe/list/move/rename/set_validity）。替代：`entry_set_entry_validity` 的 `validity_type=force_expire` 强制失效（可逆，`none` 移除） |
| `file_list_revisions` | 参数必须带 `limit`（缺了报 code=51）；对导入型 file 返回空列表 |

## 5. 调用路径（2026-11-11 结论已修正两次，以本节为准）

| 日期 | `import_create_task` / `import_describe_task` | `entry_import_connector` | 结论 |
|---|---|---|---|
| 2026-10-20 ~ 10-28 | 直调 `tool is not allowed:`；`call_tool` 包装 ✅ | 不存在 | 工具在注册表里，只是不暴露；包装能调 |
| 2026-11-11 上午 | 直调 `tool not found:`；`call_tool` ❌ `tool not found or not allowed:` | 还没出现 | 两条路径全断 |
| **2026-11-11 下午** | 同上（旧名彻底不可用） | **直调 ✅**（无需 `call_tool`） | 官方把建任务 + 查状态**合并**成新工具，旧名作废 |

⚠️ **「网关只放行 `tools/list` 里列出的工具」是当时的错误归纳**，已被推翻：
`entry_import_connector` **不在** `tools/list`（89 个工具里没有它），但直调照样成功。
所以「没列出 ≠ 调不到」，别再用 tools/list 反推能力。真正的分界是**工具名**：旧名一律
`tool not found or not allowed`，新名可用。

⚠️ `preset` 缺失的表现长得很像「工具没了」，仍然要分清：

```bash
# preset 丢了 → tools/list 只说 7 个元工具
curl -s -X POST "<endpoint>/mcp" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# 补上就回到 89 个：<endpoint>/mcp?preset=all
```

宿主连接器的 url 会变（2026-11-11 端口 `65336 → 58874`，且 `preset=all` 丢了），
所以 `api.with_preset()` 会自动补，不依赖宿主配置正确。

`api.Client` 的处理：先直调 → 被拒（JSON-RPC `tool not found:` / 文本 `tool is not allowed:` /
`tool not found or not allowed:`，三种都认）再走 `call_tool` 包装，成功过的路径记忆在进程内。
**不要绕过 Client 自己发 HTTP。**

⚠️ 被拒时 `_unwrap` 必须抛 `_PathRejected`，不能把这段文本当正常返回值往上传 ——
2026-11-11 就是因为漏认新文案，文本一路传到 `submit()` 里被当 dict 用，`AttributeError` 崩在半路，
连结果汇总都出不来。
