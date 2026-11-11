# 多源导入 skill 测试链接清单

给 `sync-multisource-doc-to-lexiang` 用的实测链接组合：**19 条候选**，覆盖企业微信（doc / 智能文档 / 表格 / 脑图）、飞书（知识库 / wiki 目录 / wiki 节点 / docx / 老版 docs）、钉钉（目录 / 在线文档 / 脑图 / 知识库入口）。按源分成 **3 个 task**（企微 8 / 飞书 5 / 钉钉 6），每源里都有 1～3 条导不了的，用来验证「整批被拒 → 自动拆单」不会连坐。

> 2026-10-22 复测：19 条里 12 条能进库，6 条失败（企微 doc 文档 3 导出受限、企微 sheet / mind 类型不支持、企微 smartpage 权限边界、飞书老版 docs、钉钉 spaces overview），1 条忽略（钉钉 amind 脑图）。

## 怎么用

一条命令生成分组配置（脚本自己读本页正文、抽链接、按源分组，**一个源一个 task**）：

```
python3 scripts/sync.py --profile test init \
    --target <目标目录的乐享链接> \
    --from "https://lexiangla.com/pages/716b7a7a82c24c47a323f6b4c026db79"
# token 留空或手填；两个 id 和 tasks 已由 init 自动写好
python3 scripts/sync.py --profile test plan              # 本地核对：应为 3 个任务 / 19 条
python3 scripts/sync.py --profile test run               # 提交 + 核对目录新增
```

⚠️ 没有 `run --dry-run` 了：2026-11-11 起 `entry_import_connector` 忽略 `dry_run`（真写库），
脚本直接拒绝执行。要预览只能 `plan`（纯本地）。

已经 init 过、只想重抽链接：`collect <本页链接> [--target <目录链接>]`（原配置备份成 `config.json.bak`）。

不想用托管方式，就自己往 `tasks[].files` 里贴链接 —— 但**同源链接要收进同一个 task**，
别一条链接一个 task。坏链接不用你手动隔离：整批被拒时脚本会自动拆单重试。

## 目标目录

建议用本页所在目录：`space_id = f5043957a670468885754af6c6773465`，
`parent_entry_id = 8e66a1f8e88e4e2b906bf692b554cca3`（或跑 `resolve` 用目录链接现查）。

## 候选清单

| # | 来源 | 链接 | 预期 |
|---|---|---|---|
| 1 | 企微 | [企微·doc 文字文档](https://doc.weixin.qq.com/doc/w3_AC0AR3j-AG8CNp2TvSs0USqefMjOn_a?scode=AJEAIQdfAAoEQzDpWHAC0AR3j-AG8) | 成功 |
| 2 | 企微 | [企微·smartpage 智能文档](https://doc.weixin.qq.com/smartpage/a1_AC0AR3j-AG8CNDFEcrMzKTYqBqyX6_a?scode=AJEAIQdfAAoG18QXZ0AC0AR3j-AG8) | 成功 |
| 3 | 企微 | [企微·doc 文档 2](https://doc.weixin.qq.com/doc/w3_ACUAQwaSACkCNcvXilq90SgGDUdWg?scode=AJEAIQdfAAoXe4630hAOwAngbdAFw) | 成功 |
| 4 | 企微 | [企微·doc 文档 3](https://doc.weixin.qq.com/doc/w3_AXwAxgbpACQCN7rFBkVJrS2ubredv?scode=AJEAIQdfAAo9Yn91UIAJUAVQbdAFw) | **失败**：`import_failed`「管理员已限制文档导出」 |
| 5 | 企微 | [企微·sheet 表格](https://doc.weixin.qq.com/sheet/e3_AGMARwawACgCNrmp3m80cTKSDqekt?scode=AJEAIQdfAAoa7jEv9mACUAQwaSACk) | **失败**：`unsupported_file_type` 该文件类型不支持导入 |
| 6 | 企微 | [企微·mind 脑图](https://doc.weixin.qq.com/mind/m4_AVIAZgaAAPcCNVHeNZHHCTOW3TdJz?scode=AJEAIQdfAAo2vCOqqrAVIAZgaAAPc) | **失败**：`unsupported_file_type` 该文件类型不支持导入 |
| 7 | 企微 | [企微·smartpage（权限边界）](https://doc.weixin.qq.com/smartpage/a1_ADoAPxTnAFkCNrJF08mt6RfyP9aXs_wiki_v2_intro?scode=AJEAIQdfAAoajDks6EAKEA0AbdAFw) | **失败**：文档级权限，「请你先打开文档」 |
| 8 | 企微 | [企微·doc 文档 4（微盘导入说明）](https://doc.weixin.qq.com/doc/w3_AE8AeAYWAEECN60aU9VvvTzOAfSrZ?scode=AJEAIQdfAAoXHsD27NAE8AeAYWAEE) | 成功 |
| 9 | 飞书 | [飞书·知识库整库](https://bwvfzliczl.feishu.cn/wiki/space/7630818956160339143) | 成功（整库递归，会展开成多条） |
| 10 | 飞书 | [飞书·wiki 目录节点](https://bwvfzliczl.feishu.cn/wiki/GIArwRwZciIES5kANXwcWPSMnKh) | 成功 |
| 11 | 飞书 | [飞书·wiki 节点](https://bwvfzliczl.feishu.cn/wiki/EpLiwSJzci9bfGk9ZBqcRthun2b) | 成功 |
| 12 | 飞书 | [飞书·docx 文档](https://bwvfzliczl.feishu.cn/docx/RjuJdbtwLolgcyx6FaCcEX7bnEc) | 成功 |
| 13 | 飞书 | [飞书·老版 docs 链接](https://bwvfzliczl.feishu.cn/docs/doccn6MpibaqsSEUpieoaITvy0b) | **失败**：`SelectionNotImportable` |
| 14 | 钉钉 | [钉钉·目录（产品规划）](https://alidocs.dingtalk.com/i/nodes/ZQYprEoWowX2gp30UArOLvOKW1waOeDk) | 成功（目录会展开子节点） |
| 15 | 钉钉 | [钉钉·目录（产品方案）](https://alidocs.dingtalk.com/i/nodes/mweZ92PV6zL27w0ksaeBA5BNVxEKBD6p) | 成功（目录会展开子节点） |
| 16 | 钉钉 | [钉钉·在线文档 1](https://alidocs.dingtalk.com/i/nodes/7QG4Yx2JpYpeM2ybcgbPXxRa89dEq3XD) | 成功 |
| 17 | 钉钉 | [钉钉·在线文档 2](https://alidocs.dingtalk.com/i/nodes/ZQYprEoWowX2gp30UQ5N1MbbW1waOeDk) | 成功 |
| 18 | 钉钉 | [钉钉·amind 脑图](https://alidocs.dingtalk.com/i/nodes/NkDwLng8ZePnRlvEc6M996BpJKMEvZBY) | **忽略**：接口 succeed 但没入库 |
| 19 | 钉钉 | [钉钉·知识库 overview 页](https://alidocs.dingtalk.com/i/spaces/15eGe18gZZR11G3N/overview) | **失败**：`nodeNotExist` |

## 怎么判断脚本是正常的

跑 `run --json`，看 `groups` 和 `totals`。⚠️ 口径在 2026-11-11 变过：旧版靠
`import_describe_task` 的 `failed_items` 能数「12 进 / 6 失败 / 1 忽略」，新接口没有明细，
所以现在的硬指标只有两类 —— **目录新增**（`ok`）与 **提交失败**（`failed`）：

1. **3 个任务 / 19 条**：一个源一个 task（`wecombot` 8 / `feishu` 5 / `dingtalk` 6）。
2. **提交失败的组会拆单**：日志出现「整批提交失败 → 拆成单条重试」+ 逐条 ✅❌，
   结果里 `split: true`。这是正常路径，不是报错。
3. `ok` = 目录实际新增条目数。飞书知识库、钉钉目录会各自展开成多条，所以条目数比链接数多，属正常。
4. `unknown` = 提交成功但目录没新增 —— 单条坏链接（如「请你先打开文档」）**也可能落在这里**，
   因为新接口不告诉你哪条坏了。所以这张清单跑完，`unknown` 非零是**预期**的，
   要确认具体哪条没进，去目录里按标题核对（或逐条单独提交一次看 `error`）。
5. 第 18 条（钉钉脑图）以前算 `ignored`，现在同样归到 `unknown`。

`ok` 和 `reused` 的比例**会变**：目标目录里已经有的条目算 `reused`（**仅当 config 写了 `name`**），
全新的算 `ok`。第一轮新增多、第二轮 `ok` 归零 —— 这是去重生效，不是脚本坏了。

## 已知边界

- **腾讯文档个人版**（`docs.qq.com`）2026-10-27 起**接口已支持**（`type=txdoc` / oneof `txdoc`），
  2026-11-11 授权修好后**实测 4/4 全部入库**，清单见下节。
- **腾讯文档企业版**（`saas.docs.qq.com`）**不支持**，本轮不考虑；`collect` / `plan` 会把它标注出来，
  别手塞进 `type=txdoc` 的 task。
- **企微 sheet 表格 / mind 脑图**：走导入接口回 `unsupported_file_type`，暂不支持（2026-10-22 实测）。
- **企微微盘分享链接**（`drive.weixin.qq.com/s?k=…`）本轮没有可用样例，待补；从微盘导入的企微文档（第 8 条）已覆盖到这条链路的下游。
- **钉钉知识库没有可用的知识库级入口**：`/i/spaces/<id>/overview` 和 `/i/spaces/<id>` 都回 `nodeNotExist`（第 19 条就是这个），要导钉钉知识库得用目录节点。
- 第 7 条报的 `ReAuthError` 是**单篇文档权限**问题，不是连接器授权过期 —— 看到它不要去重新授权。
  （对比：`txdoc` 的授权失效原文里带「腾讯文档授权」字样，那种才要重新授权。）

## 腾讯文档个人版（`type=txdoc`，2026-10-27 实测）

链接用 WorkBuddy 的「腾讯文档个人版」连接器现取，别手编（`file_id` 与链接里的 id 是 base64 互转关系，
手改一个字符就是另一篇文档，会在目标端造重复条目）：

```bash
cd ~/.workbuddy/plugins/cache/workbuddy-builtin/tencent-docs-plugin/*/skills/tencent-docs
python3 tencentdocs.py tdoc_call tencent-docs manage.search_file '{"search_key":"a","list_type":"my_doc","size":10}'
```

| # | 形态 | 链接 | 实测 |
|---|---|---|---|
| T1 | `/doc/` 在线文档 | https://docs.qq.com/doc/DU0ZBTFdGc2RmYUNq | ✅ 进库：「腾讯乐享「平台集成」使用指南」 |
| T2 | `/doc/` 在线文档 | https://docs.qq.com/doc/DU3piYlJuelNBcWlK | ✅ 进库：「通讯录同步程序使用指引」 |
| T3 | `/doc/` 在线文档 | https://docs.qq.com/doc/DZVVBb1V5S0JrUVpG | ✅ 进库：「腾讯乐享AI助手使用指南」 |
| T4 | `/sheet/` 在线表格（带 `?tab=`） | https://docs.qq.com/sheet/DZXRTUWJMdHdVWVZm?tab=000001 | ✅ 进库：「乐享知识库-微信生态内容导入脚本」（落成 `.xls`）；`?tab=` 保留不改写 |
| T5 | `/aio/` 智能文档 | https://docs.qq.com/aio/DU3VFVXpEVmVveGFE | **失败**：`source selections are not importable` |

四条放在同一个 `type=txdoc` 的 task 里即可（同源批量）；**别**和企微 / 飞书 / 钉钉混进一个 task。
T1–T4 是 2026-10-28 由凡哥提供的真实文档，已用腾讯文档连接器 `manage.query_file_info` 核实可读、非文件夹
（`file_id` = 链接里 id 去掉首位 `D` 后 base64 解码）。

准备两个东西（本仓库不写死具体 id，避免把个人库地址散出去）：

- 一份**清单页**：正文里贴 T1–T4 的链接，`collect` 直接读它；
- 一个**空的测试目录**：建在**个人知识库**里，别拿团队库试。

```bash
python3 scripts/sync.py --profile txdoc-test init --target "<你的测试目录链接>"
python3 scripts/sync.py --profile txdoc-test collect "<你的清单页链接>"
python3 scripts/sync.py --profile txdoc-test plan        # 应为 1 任务 / 4 条
python3 scripts/sync.py --profile txdoc-test run
```

> 想省事也可以跳过 `collect`，把 T1–T4 直接手写进 `tasks[0].files`（同一个 `txdoc` task）。

`collect` 实测会把 docs.qq.com 链接自动归到一个 `txdoc` task（不再需要手写 task 块）。

当前判读（**2026-11-11 真跑，4 条真实文档，通过**）：

```
[txdoc] 腾讯文档个人版 · 4 条 · task=01a1c9dcb66f7f24b8767758e9b46354 · submitted
    ✅ 目录新增 4  |  ♻️ 名字已存在 0  |  ❓ 未新增 0  |  ❌ 提交失败 0
    + f0b2ff8993dd4c66ac20937a0d2027e6  腾讯乐享「平台集成」使用指南
    + b64ea11b5f134afcbe2e22d9c547dae6  通讯录同步程序使用指引
    + 1b462fc9fcfa4d77a2f7e6b2ac771190  腾讯乐享AI助手使用指南
    + 81a1795487054084aad293ef3061f57f  乐享知识库-微信生态内容导入脚本
```

- 一次 `entry_import_connector` 提交，**24 秒内 4 条全部落库**，条目 `source.platform = txdoc`、
  `entry_type = file`、`target_type = kb_file`；sheet 落成 `.xls`；抓取瞬间 `status` 先是
  `processing`，几秒后转 `finished`。
- **同一批再跑一次**：`skip` 生效 → `ok=0 / unknown=4`，退出码 1，脚本提示先 `status` 核对。
  这是去重正常，不是失败；想再看一次「干净新增」，把 `parent_entry_id` 指到一个空子目录即可。
- 想看 MCP 原始收发：`run --debug`，逐条写进 `profiles/txdoc-test/sync.log`
- ⚠️ **T5（`/aio/`）在提交阶段不会报错**：实测把 T1 + T5 放一批提交照样拿到 `task_id`，
  T5 只表现为「目录没新增」（落进 `unknown`）。要确认某条坏链接，把它单独放一个 profile 跑一次，
  看目录有没有新增。

历史对照（2026-10-28，同一批链接）：4 条全部**任务级失败**，原文是
`… 腾讯文档导入失败，请稍后重试 (type: ReAuthError)` —— 那是乐享侧腾讯文档授权没生效，
与链接、脚本无关。授权修好后即为上面的结果。

同一个「授权没生效」有两种原文，脚本都认（`ERROR_HINTS`）：

| 形态 | 原文 |
|---|---|
| 短文案 | `腾讯文档授权已失效，请重新授权` |
| 工作流栈 | `activity error (type: ExecuteNextBatch …): list child nodes: 腾讯文档导入失败，请稍后重试 (type: wrapError, retryable: true): … (type: ReAuthError, retryable: true)` |

⚠️ 只认短文案会让工作流栈那种变成「无提示的失败」（`hints: []`），2026-10-28 踩过，已修。
