---
name: sync-multisource-doc-to-lexiang
description: 把【企业微信 / 飞书 / 钉钉 / 腾讯文档个人版】的在线文档批量或增量导入乐享知识库指定目录。走乐享 MCP 的 entry_import_connector（2026-11-11 起建任务与查状态合并成这一个工具），服务端自己抓正文、图片和附件，Agent 不需要读正文、不需要下载再上传。config.json 写清目标目录与候选链接，脚本提交后用目录前后比对确认入库。首次配置走 init / collect（默认模板 profiles/default/config.sample.json，缺配置时自动生成）。当用户提到企微、企业微信、飞书、Lark、钉钉、DingTalk、腾讯文档、docs.qq.com、乐享、导入、批量导入、增量导入、文档迁移时使用。
agent_created: true
version: "1.0.0"
category: productivity
---

# sync-multisource-doc-to-lexiang

> 变更记录见 [CHANGELOG.md](CHANGELOG.md)。**1.0.0 的关键变化**：乐享把建任务与查状态合并成
> `entry_import_connector`，入参改顶层 oneof、没有状态查询、`dry_run` 失效 —— 脚本已整体改写。

脚本干全部活：校验 → 提交 → 核对目录 → 出结果。**Agent 只做两件事：把配置建好、跑脚本。**

- 配置只管「往哪导、导什么」：两个 32 位目录 id + 按源分组的链接清单。入参形状由脚本转，
  提交时把 `{type, files}` 展开成接口要求的顶层 oneof（`{"txdoc": {"files": […]}}`）。
- 候选一律填**源端链接原文**，原样粘贴。能不能导入由乐享导入接口判定，脚本不预判、不拦截、不解释。
- 链接清单托管在乐享文档里时，`init --from` / `collect` 会自己读正文、抽链接、按源分组，
  **不用手抄、也不用手写一堆 task 块**。
- 仓库自带 `profiles/default/config.sample.json`（唯一权威模板，纯占位无凭证），根目录
  `config.example.json` 是它的软链接。**不用 mkdir / 不用 cp / 不必先跑 `init`**：缺 `config.json` 时
  `list` / `plan` / `run` 会自动从 sample 生成一份，Agent 只需改 3 处。逐字段说明见
  [`references/config-reference.md`](references/config-reference.md)。

---

## 第 0 步：先查有没有现成配置（别急着引导用户填参数）

```bash
python3 scripts/sync.py list
```

- **有就绪的 profile**（如 `default  1 任务 / 4 条 | skip | 凭证 宿主连接器`）→ 用户的同步需求
  **直接执行，不要再问源链接、目标目录或任何参数**：

```bash
python3 scripts/sync.py --profile default run        # 直接同步
python3 scripts/sync.py --profile default status     # 事后复查目标目录多了什么（别用它当预演）
```

定时 / 增量场景（「每天 20:30 同步」「再同步一次」「按原配置跑」）更是如此：
**配置只做一次，之后每次只跑 `run`**。只有用户明确说「换目标 / 换链接 / 新建一个任务」时才动 config，
且新任务用新 profile 名，别覆盖已就绪的那个。

- `list` 显示「还没有 profile」或某 profile「未就绪：…」→ 才走下面的首次配置流程。
  「未就绪」的第一行就是缺什么，照着补，不要重建配置。

---

## 首次配置：决策树（先判断手里有什么，再选命令）

| 用户给了什么 | 走哪条 | 第一条命令 |
|---|---|---|
| **A.** 一篇「放着链接清单的乐享文档」+ 目标目录链接 | 最优路径，一条命令建好配置 | `init --target <目录链接> --from <清单页链接>` |
| **B.** 直接在对话里贴了一堆源端链接 + 目标目录链接 | 先建配置，再把链接按源填进 `tasks` | `init --target <目录链接>`，然后手工填 `tasks[].files` |
| **C.** 只给目标目录链接，链接稍后再说 | 先建配置并把目录 id 写好 | `init --target <目录链接>`；`tasks` 先留模板，等链接来了用 `collect` 或手填 |
| **D.** 只有源端链接、没有目标目录 | **停下来问目标目录链接**，不要编造 32 位 id | — |
| **E.** 什么都没给 | **停下来问两样东西**：目标目录链接、链接清单（乐享文档链接或直接贴链接） | — |

> `--target` 只写 `space_id` / `parent_entry_id`，`--from` 只写 `tasks`；两个参数可同时给、也可分开给。
> 已有配置想重抽链接用 `collect`（会留 `config.json.bak`），**不要重新 `init`**（init 不覆盖）。

## 配置创建与初始化：五步，逐步验收

| 步 | 做什么 | 命令 / 动作 | 完成判据（看到什么才算过） |
|---|---|---|---|
| ① | 生成配置（**自动，无需命令**） | 首次跑 `list` / `plan` / `run` 时，缺 `config.json` 会自动从 `profiles/default/config.sample.json` 复制一份；也可显式跑 `init` | 出现 `[init] 已从 config.sample.json 生成 …`，或 `config.json` 已存在 |
| ② | 写目标目录两个 id | `init --target <乐享目录链接>`，或 `resolve <乐享目录链接>` 后手抄 | `plan` 第一行打印的两个 id 都是 **32 位十六进制**；`list` 不再显示「未就绪」 |
| ③ | 装链接并按源分组 | `init --from <清单页>` / `collect <清单页>`，或手工往 `tasks[].files` 贴链接 | `collect` 输出「抽到 N 条链接，分成 M 组」；条数与清单对得上；`plan` 里没有 ⚠️ |
| ④ | 确认凭证与策略 | `token` 留空即可（用宿主连接器）；定时任务才填个人 Token。`conflict_strategy` 保持 `skip` | `list` 显示「凭证 宿主连接器」或打码的 token |
| ⑤ | 验收 → 执行 | `plan` → `run` → 有疑问 `status` | `plan` 输出「合计 N 个任务 / M 条」且无 ⚠️；`run` 给出四档计数 |

**最快路径**：A 类素材用一条命令同时完成 ①②③（`--profile` 默认即 `default`，可省略）：

```bash
cd <skill 目录>
python3 scripts/sync.py init --target <乐享目录链接> --from <放着链接清单的乐享文档链接>
python3 scripts/sync.py plan
python3 scripts/sync.py run
```

**手工填链接时**（B 类）：同一源的全部链接收进**同一个 task 的 `files` 数组**，
别一条链接写一个 task。3 个源 = 3 个 task，不是 19 条链接 = 19 个 task。

⚠️ **没有预演**：`entry_import_connector` 忽略 `dry_run`（传了也真写库，实测），
所以 `run --dry-run` 直接拒绝执行（退出码 2）。要「先看看会提交什么」只能用 `plan`（纯本地）。
`run` 默认输出人读摘要，`--json` 让 stdout 只有 JSON。

### 完成判据（首次配置必须全过才 `run`）

- [ ] `plan` 能跑通（退出码 0），第一行两个 id 是 32 位，打印出「合计 N 个任务 / M 条」；
- [ ] `plan` 没有 ⚠️（type 与 host 不符、`saas.docs.qq.com`、`replace` + 多任务冲突）；
- [ ] 每条 `files[].id` 都是**源端链接原文**，没有剥过 `?scode=`、没有做过 URL ↔ ID 互转；
- [ ] 没有把示例占位链接（`w3_xxx`、`REPLACE_WITH_REAL_DOC_ID`）留在 `tasks` 里；
- [ ] `conflict_strategy` 显式写着 `skip`（除非用户明确要覆盖语义）；
- [ ] 命令里没有 `--dry-run`。

### 停止条件（碰到就停下问用户，别自己编）

| 现象 | 处理 |
|---|---|
| 用户没给目标目录链接 | 问用户要；**绝不编造 32 位 id，也不要把链接粘进 id 字段** |
| 用户没给任何链接 / 清单页 | 问用户要；不要用模板里的占位链接去 `run`（只会得到 `unknown`） |
| `collect` 报「没抽到任何 http(s) 链接」 | 文档正文里确实没有外链（或链接是图片/附件形式）。让用户在正文里放外链，或直接在对话里贴链接；**不要自己拼链接** |
| `collect` 列出「认不出源类型」的链接 | 逐条人工确认：能导的按 type 手工补进对应 task，明确不支持的（如 `saas.docs.qq.com`）直接不要 |
| 报「缺少乐享 MCP 凭证」 | 让用户填 `token` 或在 WorkBuddy 里连上「乐享知识库」连接器；**不降级、不 mock** |
| `plan` 有 ⚠️ | 先按提示修配置再 `run`，不要带着警告提交 |

### 不要做

1. **不改写候选链接**：不剥 `?scode=`、不做 URL ↔ ID 互转。改了就是另一个身份，会在目标端造出重复条目。
2. **不编造 id / token / 链接**，不从示例里挑一个填进去。
3. **不一条链接一个 task**；不混源（一个 task 只能一个源，混源整批被拒）。
4. **不跳过 `plan` 直接 `run`**（首次配置）；不用 `run --dry-run`。
5. **不再问用户已经填好的参数**（见第 0 步）；定时增量只跑 `run`。
6. **不整段抄脚本输出**给用户，只汇报结论。

---

## config.json：结构示意与字段

> 完整可复制模板 = [`profiles/default/config.sample.json`](profiles/default/config.sample.json)
> （根目录 `config.example.json` 是软链接）；
> 逐字段取值、来源、校验规则与多源片段 = [`references/config-reference.md`](references/config-reference.md)。

```json
{
  "token": "",
  "space_id": "<32 位知识库 id>",
  "parent_entry_id": "<32 位目录 id>",
  "conflict_strategy": "skip",
  "tasks": [
    { "type": "wecombot", "files": [ { "id": "<源端链接原文>" } ] },
    { "type": "txdoc",    "files": [ { "id": "<源端链接原文>" } ] }
  ],
  "endpoint": "https://mcp.lexiang-app.com/mcp?preset=all",
  "timeout": 30
}
```

| 字段 | 要点 |
|---|---|
| `token` | 个人 MCP Token（https://lexiangla.com/ai/claw）。**填了就用它**（定时任务）；留空自动用宿主里连着的「乐享知识库」连接器凭证 |
| `space_id` / `parent_entry_id` | 目标知识库 / 目录的 **32 位 id**。**不认链接**，用 `resolve` 或 `init --target` 查 |
| `conflict_strategy` | `skip`（默认，安全）/ `replace`（覆盖更新）/ `keep_both`（产生重复）。**别删这行**：接口层不传该参数的语义未实测、保守按 `replace` 看待，显式写死最稳。⚠️ `replace` 覆盖的前提是**身份匹配**：必须用与当初导入时相同的链接提交；换链接提交 replace 只会新增，盖不掉旧的 |
| `tasks` | 一个数组元素 = 一个导入任务 = 一次 `entry_import_connector` 调用（源类型在提交时展开成顶层 oneof 字段） |
| `tasks[].type` | `wecombot` / `feishu` / `dingtalk` / `txdoc`（腾讯文档个人版）为主；另有 `iwiki` / `wps` / `html` / `markdown` / `confluence` 接口存在但未实测。**一个任务只能一个源** |
| `tasks[].files` | `id` = 链接原文必填；`name`（指定标题，脚本靠它判 `reused`）/ `include_subpages`（展开子节点）选填 |
| `endpoint` / `timeout` | 一般不用改，可整个省略 |

字段白名单：顶层只认这 7 个、task 只认 `type/files`、file 只认 `id/name/include_subpages`；
多一个字段就报「无法识别的字段」（`_` 开头的备注字段会被忽略）。

⚠️ **`txdoc` 前置条件**：乐享侧必须先有可用的「腾讯文档」授权（租户 + 授权账号级），且该账号能打开这些文档。
没授权时任务级失败原文是 `腾讯文档授权已失效，请重新授权`，脚本会附一条 💡 提示 —— 这时**换 token、
重试、拆单都没用**，只能去乐享网页端重新授权。腾讯文档实测只接受 `/doc/` 与 `/sheet/` 链接，
智能文档 `/aio/` 会被拒（`source selections are not importable`）。**只支持个人版**：
企业版 `saas.docs.qq.com` 不支持，`collect` / `plan` 会把它标出来，别手塞进 `txdoc` 的 task。

🔴 接口的坑：**新接口提交时几乎不校验链接**（实测把一条 `/aio/` 坏链接和一条好链接放同一批，
照样拿到 `task_id`，不报错），坏链接只表现为「目录没新增」。旧版那种「整批被拒 + `failed_items` 逐条明细」
已经没有了。所以脚本的**自动拆单兜底**只在**调用本身报错**时触发：

> 某个 task 提交失败（这次调用没拿到 `task_id`，例如网关拒绝 / 参数非法 / 鉴权失败）时，脚本把该组拆成
> 「一条链接一个 task」逐条重试一次，逐条打印 ✅/❌ + `error` 原文，结果合并成一个 group（`split: true`）。
> `run --no-split` 可关掉。

所以配置可以放心写成分组式：可读性归配置。但**别指望拆单能揪出「内容导不了」的坏链接** ——
那种只会在结果里变成 `unknown`，要定位就把可疑的那条单独放一个 profile 跑一次。

---

## 命令

| 命令 | 用途 |
|---|---|
| `list` | 列 profile 和就绪状态（**永远先跑这个**） |
| `init --profile N` | 从 `profiles/default/config.sample.json` 生成 `profiles/N/config.json`（已存在不覆盖；缺配置时 `list`/`plan`/`run` 也会自动生成）。`--from <乐享文档链接>` 顺带抽链接分组；`--target <乐享目录链接>` 顺带解析目标 id |
| `collect <乐享文档链接>` | 读文档 → 抽「正文里所有 http(s) 链接」→ 按域名归源 → 重写 `tasks`（同源一个 task）。原 config 备份为 `config.json.bak`；`--target` 同时刷新两个 id |
| `resolve <乐享链接>` | 乐享目录 / 知识库链接 → `space_id` + `parent_entry_id`。一次性转换，不进 run 主路径 |
| `plan --profile N` | 本地核对 tasks 怎么分组、链接长什么样。**不联网、不提交** |
| `run --profile N` | 提交并核对目录新增。`--json` 只输出 JSON；`--no-split` 关拆单；`--wait 秒` 每组等新增的上限（默认 180，连续 8 秒无新增即提前结束）。`--dry-run` 会被拒绝 |
| `status --profile N` | 拿上次提交前的目录快照当基线，列出**现在比那时多了什么**。没有任务状态可查了，这是唯一的复查手段 |

`--profile` 放在子命令**前面**：`sync.py --profile <名字> run`。`--debug` 也是全局参数，
逐条把请求/回包写进 `profiles/<名>/sync.log`（`>>> ` 请求、`<<< ` 回包，回包截断 8000 字符）。

---

## 结果怎么读

```json
{"groups": [{"type": "txdoc", "task_id": "01a1c9dc…", "task_ids": ["01a1c9dc…"], "split": false,
             "status": "submitted", "count": 4,
             "counts": {"ok": 4, "reused": 0, "unknown": 0, "failed": 0},
             "added": [{"id": "f0b2ff8993dd4c66ac20937a0d2027e6", "name": "腾讯乐享「平台集成」使用指南"}]}],
 "totals": {"ok": 4, "reused": 0, "unknown": 0, "failed": 0}}
```

| 字段 | 含义 |
|---|---|
| `ok` | **目标目录里实际新增的条目数**（提交前后 `entry_list_children` 比对得出，唯一硬指标） |
| `reused` | 提交的链接里 `name` 早已存在于目标目录（**仅 config 里写了 `name` 才判得出**）。不是更新 |
| `unknown` | 提交成功但目录没新增。**可能是 `skip` 撞上已存在的文档，也可能压根没入库** —— 接口没有状态可查，脚本判不了 |
| `failed` | 提交阶段就失败（这次调用没拿到 `task_id`）。原因看 `error` 原文 |
| `added[]` | 新增条目的 id + 标题，可直接拿去核对 / 拼链接 |
| `split` / `task_ids` | 这一组是否走过拆单兜底；拆过就有多个 task_id，逐条 ✅❌ 已打印 |
| `hints` | 脚本按错误原文给的处理建议（只有认得出来的错误才有）。**照着 hint 做，不要自己猜原因** |

**汇报红线**：`unknown` 一律不许说成「已存在」或「跳过」。要么说「提交成功，目录没新增」，
要么先 `status` 看目录实况再下结论。汇报只说结论：几个源、多少条、新增/未新增/失败数、`task_id`。
**不要整段抄脚本输出。**

---

## 红线

1. **候选不改写**：不剥 `?scode=`、不做 URL ↔ ID 互转。改了就是另一个身份，会在目标端造出重复条目。
2. **`conflict_strategy` 不能省**：接口层不传的语义未实测、保守按 `replace` 看待（可能删掉目标目录里既有条目）；脚本虽会补 `skip`，但仍要显式写死。
3. **多任务 + `replace` 会被拒绝**（退出码 2）：replace 语义是「以本次提交为准」，多任务有互相覆盖的风险（2026-10-22 微盘场景实测未见删异源条目，但不同源行为待复核，保守处理）。要覆盖语义就拆成多个 profile。
4. **token 只写进 `config.json`**：不进任何输出、不进日志。`profiles/` 下只有 `default/config.sample.json` 与 `.gitignore` 入库，其余运行配置全被忽略；`config.sample.json` 永远保持无真实凭证 / 无真实 id，别往里填 token 再提交。
5. **凭证缺失直接报错退出**，不降级、不 mock。

---

## 排错

| 现象 | 处理 |
|---|---|
| 配置字段 / 取值 / 分组不知道怎么填 | 查 [`references/config-reference.md`](references/config-reference.md) |
| clone 后 `profiles/default/config.json` 不存在 | 正常：首次跑 `list` / `plan` / `run` 会自动从同目录 `config.sample.json` 生成，不用 mkdir / cp / init |
| `缺少乐享 MCP 凭证` | 填 `token`，或在 WorkBuddy 里连上「乐享知识库」连接器 |
| `space_id / parent_entry_id 必须是 32 位 id` | 跑 `resolve <乐享目录链接>` 拿 id，别把链接粘进去 |
| 整组 `failed` | 提交阶段就失败（没拿到 `task_id`）。脚本**已自动拆单重试过一轮**（结果里 `split: true`）：拆完还整条失败，就是这条文档自己的问题（授权没配 / 混了异源链接 `SelectionNotImportable` / 文档打不开）。看逐条 ✅❌ 的 `error` 原文 |
| 整组 `unknown`（`ok=0`，退出码 1） | 提交成功但目录没新增。先跑 `status` 看目录实况：条目已在 = `skip` 去重，正常；条目不在 = 没入库，查该源授权。**接口无状态可查，别猜** |
| `nodeNotExist` / `Connectivity failed` | 文档本身打不开或被删，不是授权问题 |
| `腾讯文档授权已失效，请重新授权` / `… ReAuthError … 腾讯文档导入失败` | **乐享侧的腾讯文档授权失效或没配**（`type=txdoc` 专属）。去乐享网页端的导入入口重新授权腾讯文档，授权账号要能打开这些文档。拆单 / 重试 / 换 token 全都无效 |
| `tool not found or not allowed: entry_import_connector` | 工具名不对或网关没放行。2026-11-11 建任务/查状态已合并成 `entry_import_connector`，旧名 `import_create_task` 必报这条。注意它**不在 `tools/list` 里但直调可用**，别拿 tools/list 反推。先确认 endpoint 带 `preset=all`（脚本已自动补），再找乐享确认是否又改名 |
| `source selections are not importable` | 链接形态不被接受。腾讯文档只实测过 `/doc/` 与 `/sheet/`，`/aio/` 智能文档会被拒 |
| 链接里带 `saas.docs.qq.com`（腾讯文档企业版） | **不支持**，腾讯文档只做个人版（`docs.qq.com`）。`plan` 会提前逐条提示，从 `files` 里删掉即可 |
| `run --dry-run` 报「不支持预演」 | 预期行为。新接口忽略 `dry_run`（传了照样写库），脚本索性拒绝执行。要看提交什么用 `plan` |
| `配置错误：… 无法识别的字段` | 字段名写错了，按 config-reference.md 的字段白名单改 |
| `collect` 说「没抽到任何 http(s) 链接」 | 文档正文里确实没有外链，或链接是图片/附件形式。手工贴进 `tasks[].files`，别自己拼链接 |
| 要看 MCP 原始请求 / 回包 | `--debug`（放在子命令前），逐条写到 `profiles/<名>/sync.log`。2026-10-28 修：此前 `--debug` 只复制 stderr，没接到 `api.Client`，日志是空的 |

---

## 测试清单

乐享文档《多源导入 skill 测试链接清单（企微 / 飞书 / 钉钉）》已备好 19 条实测链接（含边界用例）：
https://lexiangla.com/pages/716b7a7a82c24c47a323f6b4c026db79

用 `collect` 一条命令生成分组配置（3 个 task：企微 8 / 飞书 5 / 钉钉 6）：

```bash
python3 scripts/sync.py --profile test init \
    --target <目标目录链接> \
    --from "https://lexiangla.com/pages/716b7a7a82c24c47a323f6b4c026db79"
python3 scripts/sync.py --profile test plan
python3 scripts/sync.py --profile test run
```

预期：三组都会先整批提交失败，再由拆单兜底隔离出各自那条坏链接（`split: true`）—— 这是正常路径。
坏链接的失败原因在逐条 ✅❌ 的 `error` 原文里。（口径提示：旧版靠 `failed_items` 统计的
「12 进 / 6 失败 / 1 忽略」是 `import_describe_task` 时代的数，现在只能看**目录新增**与**提交失败**两类。）

腾讯文档单测（`txdoc`，2026-11-11 实测通过）：清单页与目标目录都是现成的，直接 `collect` 就能跑

```bash
python3 scripts/sync.py --profile txdoc-test init \
    --target "<你自己的乐享测试目录链接>"        # 个人库里的空目录，别用团队库试
python3 scripts/sync.py --profile txdoc-test collect \
    "<放着这 3 条腾讯文档链接的乐享清单页>"      # 或用 references/test-links.md 里的 T1–T3 直接手填
python3 scripts/sync.py --profile txdoc-test run
```

链接从 WorkBuddy 的「腾讯文档个人版」连接器现取（`skills/tencent-docs` → `tdoc_call manage.search_file`），
别手编：`file_id` 与链接里的 id 是 base64 互转关系，改一个字符就是另一篇文档。

实测结论（2026-11-11，4 条链接 = 3 篇 doc + 1 个 sheet）：一次 `entry_import_connector` 提交，
**24 秒内 4 条全部落库**，条目 `source.platform = txdoc`、`entry_type = file`，sheet 落成 `.xls`。
同一批再跑一次：`skip` 生效，目录零新增（`ok=0 / unknown=4`，退出码 1，脚本会提示先 `status` 核对）。
`/aio/` 智能文档仍回 `source selections are not importable`；企业版 `saas.docs.qq.com` 仍不支持。

---

## 自检

```bash
python3 scripts/selftest.py            # 离线，不联网不写库
python3 scripts/selftest.py --online   # 追加只读联网用例（读 txdoc-test 的目标目录）
```
