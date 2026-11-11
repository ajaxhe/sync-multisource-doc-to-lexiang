# sync-multisource-doc-to-lexiang

把**企业微信 / 飞书 / 钉钉 / 腾讯文档个人版**的在线文档，批量或增量导入乐享知识库的指定目录。
正文、图片、附件都由服务端抓取，Agent 不需要读正文、不需要下载再上传。

当前版本：`1.0.0`（变更记录见 [CHANGELOG.md](CHANGELOG.md)）。

## 怎么工作

```
config.json ──▶ sync.py ──▶ entry_import_connector（乐享 MCP）
                    │                 │
                    └── entry_list_children 前后比对 ◀── 条目落在目标目录
```

- **提交**：一次调用提交一个源的一组链接（config 里一个 task = 一次调用）。
- **核对**：提交前给目标目录拍快照，提交后轮询新增条目 —— 这是判断「进没进库」的唯一依据，
  因为 2026-11-11 起接口不再提供任务状态查询。
- **隔离**：某组提交失败时自动拆成单条重试，坏链接不连坐同组。

## 支持与边界

| 源 | 取值 | 说明 |
|---|---|---|
| 企业微信 | `wecombot` | doc / smartpage / 微盘分享链接可导；sheet、mind 回 `unsupported_file_type` |
| 飞书 | `feishu` | 知识库 / wiki 节点 / docx 可导；老版 `docs` 链接 `SelectionNotImportable` |
| 钉钉 | `dingtalk` | 目录会展开子节点；脑图导不了 |
| 腾讯文档**个人版** | `txdoc` | `docs.qq.com/doc/`、`/sheet/` 可导；`/aio/` 智能文档不支持 |
| 腾讯文档**企业版** | — | `saas.docs.qq.com` **不支持**，脚本会提前标出来 |

前提：乐享侧必须先配好对应源的连接器授权（租户级），否则任务提交后条目不会入库。

## 快速开始

```bash
# ① 初始化：一条命令写目标目录 + 从乐享文档里抽链接按源分组
python3 scripts/sync.py --profile <任务名> init \
    --target <乐享目录链接> \
    --from <放着链接清单的乐享文档链接>

# ② 需要的话填 config.json 里的 token（留空则用 Agent 宿主连着的乐享连接器凭证）
python3 scripts/sync.py --profile <任务名> plan     # 本地核对，不联网不提交
python3 scripts/sync.py --profile <任务名> run      # 提交 + 核对目录新增
python3 scripts/sync.py --profile <任务名> status   # 事后复查目录多了什么
```

⚠️ **没有预演**：`entry_import_connector` 忽略 `dry_run`（传了也真写库），所以 `run --dry-run`
会被直接拒绝（退出码 2）。要预览用 `plan`。

## 结果口径

```
✅ 目录新增 4  |  ♻️ 名字已存在 0  |  ❓ 未新增 0  |  ❌ 提交失败 0
```

| 字段 | 含义 |
|---|---|
| `ok` | 目标目录实际新增条目数（唯一硬指标） |
| `reused` | 链接的 `name` 已在目录里（仅 config 写了 `name` 才判得出） |
| `unknown` | 提交成功但目录没新增 —— 可能是去重，也可能没入库，**脚本不猜** |
| `failed` | 提交阶段就失败（没拿到 `task_id`） |

`unknown` 非零时别当成功，也别当失败：跑 `status` 看目录实况再下结论。

## 目录结构

```
SKILL.md                     Agent 执行说明（含排错表、测试清单）
CHANGELOG.md                 变更记录
scripts/sync.py              主流程：校验 → 提交 → 核对目录 → 汇总
scripts/api.py               MCP 传输层（JSON-RPC / SSE / 路径回退 / preset 兜底）
scripts/selftest.py          离线自检（31 例）+ 只读联网用例
references/import-api.md     接口事实：入参形状、返回形态、调用路径对照
references/test-links.md     实测链接清单与判读标准
profiles/<名字>/config.json  运行配置（含 token，已在 .gitignore 里排除）
```

## 自检

```bash
python3 scripts/selftest.py            # 离线，约 3 秒，不联网不写库
python3 scripts/selftest.py --online   # 追加只读联网用例（需要有效凭证）
```

## License

MIT
