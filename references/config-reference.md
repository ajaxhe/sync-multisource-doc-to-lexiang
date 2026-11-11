# config.json 参数说明

> 配套模板：[`config.example.json`](../config.example.json)（skill 根目录，**唯一权威默认模板**）。
> 本文只讲「配置怎么填」。接口事实见 [import-api.md](import-api.md)，执行流程、结果口径与排错见 [SKILL.md](../SKILL.md)。

## 一、30 秒上手：只改 3 处

| # | 改什么 | 怎么拿到 |
|---|---|---|
| 1 | `space_id` + `parent_entry_id` | 两个 32 位 id。让脚本自己填最快：`sync.py --profile default init --target <乐享目录链接>`；或先 `init` 再 `resolve <乐享目录链接>` 手动抄。**只认 32 位 id，粘链接必报错** |
| 2 | `tasks[].files[].id` | 源端链接原文，**原样粘贴**。链接清单若托管在乐享文档里，用 `collect <乐享文档链接>` 自动抽链接、按源分组，不用手抄 |
| 3 | `token` | **可留空**。留空 = 用宿主里连着的「乐享知识库」连接器凭证；只有定时任务 / 无人值守场景才需要填个人 MCP Token（https://lexiangla.com/ai/claw） |

然后固定三步：`plan`（本地核对，不联网）→ `run`（提交并真写库）→ 有疑问再 `status`（复查目录）。

⚠️ 模板里的链接是占位符，**不换掉就 `run` 不会入库**（结果记为 `unknown`）。
⚠️ **没有预演**：`run --dry-run` 会被直接拒绝（退出码 2）—— 新接口忽略 `dry_run`，传了照样写库。要预览只能 `plan`。
⚠️ `plan` / `run` 都会先校验配置；`space_id` / `parent_entry_id` 还是占位符时直接报「未填」，不会联网、更不会提交。

## 二、配置在哪、谁生成

| 路径 | 说明 |
|---|---|
| `config.example.json` | 默认模板（随 skill 入库，不含任何真实凭证 / id）。`init` 生成配置时读的就是它 |
| `profiles/<名字>/config.json` | 真正被读取的运行配置。**一个 profile = 一个独立导入任务**，名字自己取（默认 `default`） |
| `profiles/<名字>/config.json.bak` | `collect` 覆盖 `tasks` 前的自动备份 |
| `profiles/<名字>/state.json` | `run` 写下的提交前目录快照，供 `status` 复查；不要手改 |
| `profiles/<名字>/sync.log` | 仅 `--debug` 时生成，含原始请求 / 回包 |

```bash
# 方式 A（推荐）：脚本生成，模板来自 config.example.json
python3 scripts/sync.py --profile default init

# 方式 B：直接复制，效果相同
mkdir -p profiles/default && cp config.example.json profiles/default/config.json
```

`profiles/` 整个目录已在 `.gitignore` 里 —— **config.json 会含 token，绝不提交到任何仓库**。

## 三、顶层字段

| 字段 | 必填 | 取值 / 默认 | 说明 | 填错的后果 |
|---|---|---|---|---|
| `token` | 否 | `""` | 个人 MCP Token。留空时脚本从宿主注入的 `CODEBUDDY_MCP_CONFIG` 里找名字含 `lexiang` 的连接器，用它的 url + 认证头（会话级，不落盘）；两者都没有就报错退出，**不降级、不 mock** | 凭证无效 → `鉴权失败`，退出码 1 |
| `space_id` | ✅ | 32 位十六进制 | 目标知识库 id。`resolve` / `init --target` / `collect --target` 都能解析 | 非 32 位 / 占位未换 → `配置错误`，退出码 2 |
| `parent_entry_id` | ✅ | 32 位十六进制 | 目标目录 id（知识库根节点也行）。同上 | 同上 |
| `conflict_strategy` | ✅ 建议显式写 | `skip`（脚本默认）/ `replace` / `keep_both` | 命中同名 / 同源条目时的处理 | 见下方专节 |
| `tasks` | ✅ | 数组，至少 1 个 `{type, files}` | 一个数组元素 = 一次 `entry_import_connector` 调用 | 空 / 非数组 → 退出码 2 |
| `endpoint` | 否 | `https://mcp.lexiang-app.com/mcp?preset=all` | 乐享 MCP 地址。缺 `preset=all` 时脚本会自动补上 | 一般不用改 |
| `timeout` | 否 | `30` | 单次 HTTP 超时（秒） | 一般不用改 |

### `conflict_strategy` 三档

| 值 | 语义 | 用在哪 |
|---|---|---|
| `skip`（默认，安全） | 命中既有条目就跳过，重复跑不产生重复条目 | 批量导入、定时增量同步 |
| `replace` | 覆盖更新：同源命中时刷新条目内容 | 需要刷新内容的单任务场景 |
| `keep_both` | 保留两份，会产生重复条目 | 一般不推荐 |

三条硬规则：

1. **别删这一行**。脚本在字段缺失时确实会补 `skip`，但接口层「不传该参数」的语义未实测、保守按 `replace` 看待 —— 显式写死最稳。
2. **`replace` + 多个 task 会被脚本直接拒绝**（退出码 2）：replace 是「以本次提交为准」，多任务之间可能互相删除条目。要覆盖语义就拆成多个 profile。
3. **`replace` 的前提是身份匹配**：必须用与当初导入时**完全相同**的链接提交。换链接、换管道提交 replace 只会新增，盖不掉旧的。

## 四、`tasks[]` / `files[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `tasks[].type` | ✅ | 源类型，**一个任务只能一个源**（混源实测回 `SelectionNotImportable`，整任务被拒）。跨源就多写几个 task |
| `tasks[].files` | ✅ | 待导入链接列表，至少 1 条 |
| `files[].id` | ✅ | 链接原文，**原样粘贴**：不剥 `?scode=`、不做 URL ↔ ID 互转、不改写。改了就是另一个身份，会在目标端造出重复条目 |
| `files[].name` | 否 | 指定导入后的标题。额外作用：脚本**只靠它**判「名字已存在（`reused`）」；不写 `name` 时一律不猜 |
| `files[].include_subpages` | 否（bool） | 展开子节点（飞书 wiki、钉钉目录等文件夹型源）。一条链接可能落成多个条目 |

**分组原则：一个源一个 task**（4 个源 = 4 个 task，不是 19 条链接 = 19 个 task）。同源的全部链接收进同一个 `files` 数组 —— 可读性归配置，脚本提交时按 task 整批发。

### `type` 取值

| type | 源 | 可导链接 | 已知边界 |
|---|---|---|---|
| `wecombot` | 企业微信 | `doc.weixin.qq.com` 的 doc / smartpage / 微盘分享链接 | `sheet`、`mind` 回 `unsupported_file_type` |
| `feishu` | 飞书 | 知识库 / wiki 节点 / docx（`feishu.cn`、`larksuite.com`、`larkoffice.com`） | 老版 `/docs/` 链接回 `SelectionNotImportable` |
| `dingtalk` | 钉钉 | `alidocs.dingtalk.com` 文档 / 目录 | 目录会展开子节点；脑图导不了 |
| `txdoc` | 腾讯文档**个人版** | `docs.qq.com/doc/…`、`docs.qq.com/sheet/…` | `/aio/` 智能文档不支持；企业版 `saas.docs.qq.com` **不支持**（`collect` / `plan` 会标出来）；必须先配好乐享侧租户级「腾讯文档」授权 |
| `iwiki` / `wps` / `html` / `markdown` / `confluence` | 接口层存在，**未实测** | 无稳定域名特征 | `collect` 无法自动归源（会列为「认不出源类型」），手工配置时也请自行验证 |

> 前提：乐享侧必须先配好对应源的**租户级连接器授权**，否则任务提交成功也不会入库。
> 这一层授权和 WorkBuddy 里的「腾讯文档个人版」连接器是两套东西，重连后者没用。

## 五、可复制的配置片段

### 最小：单源单条（改 2 个 id + 1 条链接即可跑）

```json
{
  "token": "",
  "space_id": "f5043957a670468885754af6c6773465",
  "parent_entry_id": "e4b9ce12d924438196be13224341817f",
  "conflict_strategy": "skip",
  "tasks": [
    { "type": "txdoc", "files": [ { "id": "https://docs.qq.com/doc/DU3BiQ0ZsVEVCcEVN" } ] }
  ]
}
```

`endpoint` / `timeout` 可以整个省略，脚本会给默认值。

### 多源：四源各一个 task

```json
{
  "token": "",
  "space_id": "<32 位>",
  "parent_entry_id": "<32 位>",
  "conflict_strategy": "skip",
  "tasks": [
    { "type": "wecombot", "files": [ { "id": "https://doc.weixin.qq.com/doc/w3_A?scode=a" } ] },
    { "type": "feishu",   "files": [ { "id": "https://x.feishu.cn/wiki/B", "include_subpages": true } ] },
    { "type": "dingtalk", "files": [ { "id": "https://alidocs.dingtalk.com/i/nodes/C" } ] },
    { "type": "txdoc",    "files": [ { "id": "https://docs.qq.com/doc/D" } ] }
  ]
}
```

### 定时任务 / 无人值守

把 `token` 填上个人 MCP Token（否则依赖宿主会话注入的连接器凭证，脱离会话就没了），保持 `conflict_strategy: "skip"` —— 配置只做一次，之后每次只跑 `run`。

## 六、脚本会怎样改写配置

| 命令 | 对 config.json 的影响 |
|---|---|
| `init` | **仅当不存在时**生成，已存在一律不覆盖 |
| `init --target` / `collect --target` | 只写 `space_id` + `parent_entry_id`，不动 `tasks` |
| `init --from` / `collect` | **整体重写 `tasks`**（读乐享文档 → 抽所有 http(s) 链接 → 按域名归源 → 同源一个 task）；覆盖前自动留 `config.json.bak`。手填的 `name` / `include_subpages` 会丢，需要事后补 |
| `plan` / `run` / `status` | 不改配置 |

`collect` 认不出源类型的链接**不会写进 tasks**，会在输出里逐条列出并说明原因（如腾讯文档企业版）。需要的话手工补进对应 task。

## 七、校验规则与退出码

- **字段白名单**：顶层只认 `token / space_id / parent_entry_id / conflict_strategy / tasks / endpoint / timeout`；task 只认 `type / files`；file 只认 `id / name / include_subpages`。多一个字段就报「无法识别的字段」。**以 `_` 开头的字段会被忽略**，可以用它写本地备注（例：`"_note": "2026-11 第 2 批"`）。
- 两个 id 必须是 32 位十六进制；占位符（`<…>` 或含「在此填入」）会被判为「未填」。
- 退出码：`0` 正常 / `1` 有提交失败或一条都没新增 / `2` 用法或配置错误。

## 八、安全红线

1. `token` 只写进 `profiles/<名字>/config.json`，不进任何输出、不进日志；`list` 会打码显示。
2. `config.example.json` 是模板，**永远保持无真实凭证、无真实 id**。
3. `--debug` 生成的 `sync.log` 可能含请求细节与链接，别随意外传。
