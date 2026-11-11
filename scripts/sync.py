#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync.py —— 多源在线文档 → 乐享知识库，批量 / 增量导入。

⚠️ 2026-11-11 起乐享把 `import_create_task` + `import_describe_task` 合并成
**`entry_import_connector`** 一个工具。带来的三个硬约束，脚本形态就是围着它们长的：

  ① 入参是**顶层 oneof**：`{"space_id","parent_entry_id","conflict_strategy","txdoc":{"files":[…]}}`
     —— 源类型直接当字段名，旧的 `{"type":"txdoc","files":[…]}` 会只回一句 oneof 报错。
  ② **没有状态查询**：回包只有 task_id，`pending/succeed/failed` 这套没了。
     所以「到底进没进库」只能靠**目标目录前后比对**（entry_list_children）。
  ③ **没有 dry_run**：传了也真写库（实测）。所以 `run --dry-run` 直接拒绝执行，
     要预览就用离线的 `plan`。

    python3 scripts/sync.py --profile <名> init [--from <乐享文档>] [--target <乐享目录>]
    python3 scripts/sync.py --profile <名> collect <乐享文档链接> [--target <乐享目录链接>]
    python3 scripts/sync.py --profile <名> resolve <乐享链接>  # ② 链接 → space_id / parent_entry_id
    python3 scripts/sync.py --profile <名> plan                # ③ 本地核对 tasks，不联网不提交
    python3 scripts/sync.py --profile <名> run                 # ④ 提交 + 核对目录新增
    python3 scripts/sync.py --profile <名> status              #    事后复查目标目录，不要重跑
    python3 scripts/sync.py list

分组：**一个源一个 task**，同源的链接全收进该 task 的 `files`。
整批提交失败时自动拆成单条重试一次，坏链接不连坐同组；`--no-split` 关闭。

结果口径（重要）：脚本不再有「新增 / 已存在」的权威明细，只能给
`目录新增条数` + `名字已存在条数` + `提交了但目录没新增（原因不明）`。
后者是 skip 去重与「悄悄没入库」的混合体，判不了就别硬判 —— 用 `status` 看目录实际内容。

退出码：0 正常 / 1 有提交失败、或一条都没新增 / 2 用法或配置错误。
"""

import argparse
import collections
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import api      # noqa: E402

ROOT = os.path.dirname(HERE)
PROFILES = os.path.join(ROOT, "profiles")

# 一个源一个 task：同一个 type 的链接全部收进该 task 的 files 数组。
# 组内某条坏链接会在运行时触发「自动拆单」兜底，不会连坐同组的其他文档。
CONFIG_TEMPLATE = """{
  "token": "",
  "space_id": "<32位目标知识库 id，用 resolve 命令查>",
  "parent_entry_id": "<32位目标目录 id，用 resolve 命令查>",
  "conflict_strategy": "skip",
  "tasks": [
    {
      "type": "wecombot",
      "files": [
        { "id": "https://doc.weixin.qq.com/doc/w3_xxx?scode=xxx" },
        { "id": "https://doc.weixin.qq.com/sheet/e3_xxx?scode=xxx" }
      ]
    },
    {
      "type": "feishu",
      "files": [
        { "id": "https://xxx.feishu.cn/docx/xxx" }
      ]
    },
    {
      "type": "dingtalk",
      "files": [
        { "id": "https://alidocs.dingtalk.com/i/nodes/xxx" }
      ]
    },
    {
      "type": "txdoc",
      "files": [
        { "id": "https://docs.qq.com/doc/xxxx" },
        { "id": "https://docs.qq.com/sheet/xxxx" }
      ]
    }
  ],
  "endpoint": "https://mcp.lexiang-app.com/mcp?preset=all",
  "timeout": 30
}
"""

GITIGNORE = ("# 本目录含密钥（config.json 的 token）与日志，严禁提交到任何代码/skill 托管平台。\n"
             "*\n!.gitignore\n")

# 导入工具名。2026-11-11 起建任务与查状态合并成这一个，旧的 import_create_task /
# import_describe_task 已下线（直呼回 `tool not found or not allowed`）。
IMPORT_TOOL = "entry_import_connector"
# 核对入库结果只能靠它：目标目录直接子条目列表
LIST_TOOL = "entry_list_children"

STRATEGIES = ("skip", "replace", "keep_both")
KNOWN_KEYS = ("token", "space_id", "parent_entry_id", "conflict_strategy",
              "tasks", "endpoint", "timeout")
TASK_KEYS = ("type", "files")
FILE_KEYS = ("id", "name", "include_subpages")

_ID_RX = re.compile(r"^[0-9a-fA-F]{32}$")

# 源类型速查：label 用于展示；hosts **只用于 plan 的「type 填错」提示与 collect 的自动分组，不参与提交**。
# type 是任务级参数，提交时永远用 config 里显式写的值 —— 能不能导由乐享导入接口判定。
# `exclude` 用来挡掉同域下的其它产品线：先判 exclude 再判 hosts，避免「看着像就归进来」。
KNOWN_TYPES = {
    "wecombot":  {"label": "企业微信", "hosts": ("weixin.qq.com",)},
    "feishu":    {"label": "飞书",     "hosts": ("feishu.cn", "larksuite.com", "larkoffice.com")},
    "dingtalk":  {"label": "钉钉",     "hosts": ("dingtalk.com",)},
    # 腾讯文档个人版（docs.qq.com）2026-10-27 实测 type=txdoc 已可用。
    # ⚠️ 腾讯文档企业版（saas.docs.qq.com）**不支持**，必须 exclude 掉：
    #    host 是子串匹配，`saas.docs.qq.com` 含 `docs.qq.com`，不挡就会被误归成 txdoc，
    #    然后拿着必败的链接去提交。
    "txdoc":     {"label": "腾讯文档个人版", "hosts": ("docs.qq.com",),
                  "exclude": ("saas.docs.qq.com",)},
    # 以下类型接口可用，但没有稳定的 host 特征，不做 host 提示
    "iwiki":     {"label": "iWiki",    "hosts": ()},
    "wps":       {"label": "WPS",      "hosts": ()},
    "html":      {"label": "HTML",     "hosts": ()},
    "markdown":  {"label": "Markdown", "hosts": ()},
    "confluence": {"label": "Confluence", "hosts": ()},
}

# 认得出但已知导不了的 host → 一句人话说明（collect / plan 用它把「为什么被跳过」讲清楚）
UNSUPPORTED_HOSTS = (
    ("saas.docs.qq.com", "腾讯文档企业版暂不支持导入（只支持个人版 docs.qq.com）"),
)

# 任务级失败里能直接指路「下一步干什么」的错误原文 → 处理建议。只给提示，不改判定。
# ⚠️ 别把裸 `ReAuthError` 加进来：企微单篇文档没权限也报这个（见 references/test-links.md 第 7 条），
#    那种情况重新授权是白费功夫。所以只匹配「腾讯文档」这个具体上下文。
#
# 腾讯文档同一个「授权没生效」有两种原文，都得认：
#   ① 短文案：`腾讯文档授权已失效，请重新授权`
#   ② 工作流栈：`activity error (type: ExecuteNextBatch …): list child nodes: 腾讯文档导入失败，
#      请稍后重试 (type: wrapError …): … (type: ReAuthError …)`
# 只认 ① 会让 ② 变成「无提示的失败」，排查时看不出下一步。
ERROR_HINTS = (
    ("腾讯文档授权",
     "乐享侧的腾讯文档授权失效或未配置。这是**租户级连接器**的授权，不是 WorkBuddy 里的"
     "「腾讯文档个人版」连接器（两套东西，重连后者没用）。去乐享管理后台/导入入口把「腾讯文档」"
     "重新授权，授权用的腾讯文档账号必须能打开这些文档。换 token / 重试 / 拆单都不会好转。"),
    ("腾讯文档导入失败",
     "同上：乐享侧的腾讯文档授权没生效（原文里带 ReAuthError）。这是租户级连接器授权，"
     "与 WorkBuddy 的腾讯文档连接器无关，也与链接本身无关 —— 先把授权状态确认成「已连接」再重跑。"),
    ("selections are not importable",
     "这种链接形态导入接口不认。腾讯文档目前只实测通过 /doc/ 与 /sheet/ 链接，"
     "智能文档 /aio/ 会被这一条拒掉。"),
    ("tool not found or not allowed",
     "乐享 MCP 网关没放行 `entry_import_connector`（2026-11-11 起建任务/查状态合并成它，"
     "旧的 import_create_task 已下线）。先确认 endpoint 带 preset=all（脚本已自动补；"
     "补了仍不行就不是这个原因），再找乐享确认服务端是否改了工具名 —— 本地改配置、换 token 都解决不了。"),
)


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

class ConfigError(RuntimeError):
    """配置有问题 —— 属于用法错误，退出码 2。"""


class Log(object):
    """进度一律走 stderr，stdout 只留给最终结果（--json 时尤其重要）。"""

    def __init__(self, path=None):
        self.path = path

    def __call__(self, msg=""):
        sys.stderr.write(str(msg) + "\n")
        sys.stderr.flush()
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
            except OSError:
                pass


def _blank(v):
    s = str(v or "").strip()
    return not s or s.startswith("<") or "在此填入" in s


def label_of(t):
    return (KNOWN_TYPES.get(t) or {}).get("label") or t or "(未知)"


def guess_type(url):
    """按 host 猜源，只给 plan 提示与 collect 分组用；认不出返回 None。

    先判该类型的 `exclude`：同域下的其它产品线（如腾讯文档企业版 saas.docs.qq.com）
    不能因为域名包含关系被误归进个人版。
    """
    low = str(url or "").strip().lower()
    if not low:
        return None
    for name, meta in KNOWN_TYPES.items():
        if any(h in low for h in meta.get("exclude") or ()):
            continue
        if any(h in low for h in meta["hosts"]):
            return name
    return None


def unsupported_reason(url):
    """认得出但已知导不了的 host → 一句人话；其它返回 None。"""
    low = str(url or "").strip().lower()
    for host, why in UNSUPPORTED_HOSTS:
        if host in low:
            return why
    return None


def hints_of(rec):
    """按错误原文给处理建议（含拆单后的子任务）。只出提示，不影响成败判定。"""
    texts = [str((rec.get("data") or {}).get("err_message") or ""), str(rec.get("error") or "")]
    for child in rec.get("children") or []:
        texts.append(str((child.get("data") or {}).get("err_message") or ""))
        texts.append(str(child.get("error") or ""))
    blob = " ".join(texts).lower()
    return [tip for key, tip in ERROR_HINTS if key.lower() in blob]


# 抽链接：markdown 内联链接 `](url)` 优先，其次裸 URL；两者都收，去重保序
_MD_LINK_RX = re.compile(r"\]\(\s*(https?://[^)\s]+?)\s*\)")
_BARE_URL_RX = re.compile(r"https?://[^\s<>()\[\]\"'）】，、]+")
_URL_TAIL = ".,;:!?）】，。、;"


def extract_links(text):
    """从一篇 markdown / 纯文本里抽外部链接，去重（保序），去掉尾部标点。"""
    text = str(text or "")
    found, seen = [], set()
    for rx in (_MD_LINK_RX, _BARE_URL_RX):
        for m in rx.finditer(text):
            u = m.group(1) if rx is _MD_LINK_RX else m.group(0)
            u = u.strip().rstrip(_URL_TAIL)
            if not u or u in seen:
                continue
            seen.add(u)
            found.append(u)
    return found


def _type_order(t):
    """已知源的展示顺序；未知源排在后面，保持出现顺序。"""
    known = list(KNOWN_TYPES)
    return known.index(t) if t in known else len(known)


def group_links(links):
    """链接 → ({type: [url…]}, [认不出源类型的 url…])。同 type 收进一个组。"""
    groups, unknown, seen = {}, [], set()
    for u in links or []:
        u = str(u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        t = guess_type(u)
        if not t:
            unknown.append(u)
            continue
        groups.setdefault(t, []).append(u)
    ordered = collections.OrderedDict(
        (t, groups[t]) for t in sorted(groups, key=_type_order))
    return ordered, unknown


def build_tasks(links):
    """链接列表 → config 的 tasks（一个源一个 task）。"""
    groups, _unknown = group_links(links)
    return [{"type": t, "files": [{"id": u} for u in urls]} for t, urls in groups.items()]


# --------------------------------------------------------------------------
# 配置加载 / 校验（config 的 tasks 是本脚本自己的形状，提交前由 import_args() 转成 oneof）
# --------------------------------------------------------------------------

def _load_raw(profile):
    cfg_path = os.path.join(PROFILES, profile, "config.json")
    if not os.path.exists(cfg_path):
        raise ConfigError("profile「%s」不存在：%s\n先跑 `sync.py --profile %s init`。"
                          % (profile, cfg_path, profile))
    try:
        with open(cfg_path, encoding="utf-8") as f:
            raw = json.load(f)
    except ValueError as e:
        raise ConfigError("config.json 不是合法 JSON：%s" % e)
    if not isinstance(raw, dict):
        raise ConfigError("config.json 顶层必须是对象")
    return raw


def load_creds(profile):
    """只取凭证相关字段（resolve 命令用，不要求 tasks 已填好）。"""
    raw = _load_raw(profile)
    token = raw.get("token")
    return {
        "profile": profile,
        "dir": os.path.join(PROFILES, profile),
        "token": "" if _blank(token) else str(token).strip(),
        "endpoint": str(raw.get("endpoint") or "").strip() or api.DEFAULT_ENDPOINT,
        "timeout": raw.get("timeout") or 30,
    }


def _check_id(value, field):
    s = str(value or "").strip()
    if _blank(s):
        raise ConfigError("%s 未填：用 `resolve <乐享目录链接>` 查，把 32 位 id 填进来。" % field)
    if not _ID_RX.match(s):
        raise ConfigError("%s 必须是 32 位 id，当前是「%s」。它不是链接 —— "
                          "跑 `sync.py --profile <名> resolve <乐享目录链接>` 拿到 id 再填。" % (field, s[:80]))
    return s.lower()


def _check_tasks(tasks):
    if not isinstance(tasks, list) or not tasks:
        raise ConfigError("tasks 未填：至少一个 {type, files} 对象。type 取值见 SKILL.md。")
    out = []
    for i, task in enumerate(tasks, 1):
        where = "tasks[%d]" % (i - 1)
        if not isinstance(task, dict):
            raise ConfigError("%s 必须是对象（{type, files}），当前是 %r" % (where, task))
        unknown = [k for k in task if k not in TASK_KEYS and not k.startswith("_")]
        if unknown:
            raise ConfigError("%s 有无法识别的字段：%s（可用：%s）" % (where, "、".join(unknown),
                                                                       "、".join(TASK_KEYS)))
        t = str(task.get("type") or "").strip()
        if not t:
            raise ConfigError("%s.type 未填：源类型（wecombot / feishu / dingtalk / txdoc …）" % where)
        files = task.get("files")
        if not isinstance(files, list) or not files:
            raise ConfigError("%s.files 未填：至少一条 {\"id\": \"<源端链接原文>\"}" % where)
        checked = []
        for j, f in enumerate(files):
            fwhere = "%s.files[%d]" % (where, j)
            if not isinstance(f, dict):
                raise ConfigError("%s 必须是对象（{\"id\": \"<链接>\"}），当前是 %r" % (fwhere, f))
            unknown = [k for k in f if k not in FILE_KEYS and not k.startswith("_")]
            if unknown:
                raise ConfigError("%s 有无法识别的字段：%s（可用：%s）"
                                  % (fwhere, "、".join(unknown), "、".join(FILE_KEYS)))
            fid = str(f.get("id") or "").strip()
            if not fid:
                raise ConfigError("%s.id 未填：源端链接原文，原样粘贴" % fwhere)
            item = {"id": fid}
            if str(f.get("name") or "").strip():
                item["name"] = str(f["name"]).strip()
            if f.get("include_subpages") is not None:
                item["include_subpages"] = bool(f["include_subpages"])
            checked.append(item)
        out.append({"type": t, "files": checked})
    return out


def load_config(profile):
    raw = _load_raw(profile)
    unknown = [k for k in raw if k not in KNOWN_KEYS and not k.startswith("_")]
    if unknown:
        raise ConfigError("config.json 里有无法识别的字段：%s（可用字段：%s）"
                          % ("、".join(unknown), "、".join(KNOWN_KEYS)))

    strategy = str(raw.get("conflict_strategy") or "skip").strip().lower()
    if strategy not in STRATEGIES:
        raise ConfigError("conflict_strategy 只能是 %s，当前是「%s」"
                          % (" / ".join(STRATEGIES), strategy))

    token = raw.get("token")
    return {
        "profile": profile,
        "dir": os.path.join(PROFILES, profile),
        "token": "" if _blank(token) else str(token).strip(),
        "space_id": _check_id(raw.get("space_id"), "space_id"),
        "parent_entry_id": _check_id(raw.get("parent_entry_id"), "parent_entry_id"),
        "conflict_strategy": strategy,
        "tasks": _check_tasks(raw.get("tasks")),
        "endpoint": str(raw.get("endpoint") or "").strip() or api.DEFAULT_ENDPOINT,
        "timeout": raw.get("timeout") or 30,
    }


def replace_conflict(cfg):
    """replace + 多任务 = 批次之间互相删除，返回冲突说明；无冲突返回 None。"""
    if cfg["conflict_strategy"] != "replace" or len(cfg["tasks"]) < 2:
        return None
    dup = [t for t, n in collections.Counter(x["type"] for x in cfg["tasks"]).items() if n > 1]
    extra = ("（同 type 多任务：%s）" % "、".join(dup)) if dup else ""
    return ("有 %d 个任务而 conflict_strategy=replace —— replace 的语义是「本批提交 = 目录保留」，"
            "任务之间会互相删除对方条目%s。改用 skip，或拆成多个 profile。"
            % (len(cfg["tasks"]), extra))


# --------------------------------------------------------------------------
# 状态文件
# --------------------------------------------------------------------------

def read_state(cfg):
    path = os.path.join(cfg["dir"], "state.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(cfg, state):
    os.makedirs(cfg["dir"], exist_ok=True)
    path = os.path.join(cfg["dir"], "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 提交 + 轮询
# --------------------------------------------------------------------------

def import_args(cfg, task):
    """构造 entry_import_connector 入参。

    ⚠️ 2026-11-11 起接口换形：源类型不再是 `type` + `files`，而是**顶层 oneof**，
    成员名就是源类型，值里带 files：
        {"space_id":…, "parent_entry_id":…, "txdoc": {"files": [{"id": …}]}}

    写成旧的 `{"type":"txdoc","files":[…]}` 不会报「字段不对」—— 未知字段被静默丢弃，
    只剩一句 `source: exactly one field is required in oneof`，看不出所以然。
    """
    return {
        "space_id": cfg["space_id"],
        "parent_entry_id": cfg["parent_entry_id"],
        # 实测 skip 生效（重复导入不产生重复条目）；不传的行为没验，所以永远显式下发
        "conflict_strategy": cfg["conflict_strategy"],
        task["type"]: {"files": task["files"]},
    }


def submit(client, cfg, task):
    """提交一个任务，成功只代表**已受理**（拿到 task_id），不代表已入库。

    ⚠️ 别加 `dry_run`：新工具忽略它，传了照样写库 —— 加个「预演」开关反而误导人。
    """
    rec = {"type": task["type"], "count": len(task["files"]), "inputs": task["files"],
           "task_id": "", "task_ids": [], "status": "", "error": "",
           "added": {}, "reused": 0}
    try:
        data = client.call(IMPORT_TOOL, import_args(cfg, task))
    except api.ApiError as e:
        rec["status"] = "failed"
        rec["error"] = str(e)
        return rec

    # 接口可能回纯文本（工具被拒/服务端异常），不能假定一定是 dict ——
    # 2026-11-11 就因为这个直接 AttributeError 崩在断言之外，连结果都打不出来
    if not isinstance(data, dict):
        rec["status"] = "failed"
        rec["error"] = "接口回包不是对象（原文）：%s" % str(data)[:300]
        return rec

    rec["task_id"] = str(data.get("task_id") or "")
    if not rec["task_id"]:
        rec["status"] = "failed"
        rec["error"] = "接口没回 task_id：%s" % json.dumps(data, ensure_ascii=False)[:200]
        return rec
    rec["task_ids"] = [rec["task_id"]]
    rec["status"] = "submitted"
    return rec


def list_children(client, parent_id, limit=100, max_pages=20):
    """目标目录下的条目 {entry_id: name}。分页用 page_token，本页为空或没 token 即止。"""
    out, token, pages = {}, "", 0
    while pages < max_pages:
        args = {"parent_id": parent_id, "limit": limit}
        if token:
            args["page_token"] = token
        data = client.call(LIST_TOOL, args) or {}
        entries = data.get("entries") or []
        for e in entries:
            if isinstance(e, dict) and e.get("id"):
                out[e["id"]] = e.get("name") or ""
        token = str(data.get("next_page_token") or "")
        pages += 1
        if not entries or not token:
            break
    return out


def wait_new_entries(client, parent_id, before, wait, log, quiet=8, interval=3):
    """轮询目录直到「连续 quiet 秒无新增」或到 wait 上限，返回新增 {entry_id: name}。

    这是**唯一**的入库判据（没有任务状态可查）。按条目计不按提交条数计 ——
    文件夹型源会展开成多条，1 条链接可能落 3 个条目。
    """
    deadline = time.time() + max(wait, quiet + interval)
    added, last_change = {}, time.time()
    while True:
        try:
            now = list_children(client, parent_id)
        except api.ApiError as e:
            log("    核对目录失败：%s" % e)
            break
        fresh = {k: v for k, v in now.items() if k not in before}
        # ⚠️ 只有「又多了」才算有变化。拿 fresh 非空当变化会让计时器每轮刷新，
        #    目录稳定后也一直等到 deadline（实测把整个 run 拖到 2 分钟以上被掐死）。
        if len(fresh) > len(added):
            added, last_change = fresh, time.time()
            log("    目录新增 %d 条 …" % len(added))
        if time.time() - last_change >= quiet or time.time() >= deadline:
            break
        time.sleep(interval)
    return added


def mark_reused(rec, seen_names, files):
    """能判就判「已存在被 skip」：只有 config 里写了 name 才有依据。

    没写 name 的文件不猜 —— 硬猜会把「没入库」说成「已存在」，正是最坑的假成功。
    """
    names = set(n for n in seen_names if n)
    if not names:
        return
    rec["reused"] = sum(1 for f in files if f.get("name") and f["name"] in names)


# --------------------------------------------------------------------------
# 结果归纳
# --------------------------------------------------------------------------

def is_task_level_failure(rec):
    """提交阶段就失败。

    新工具没有条目级明细（没有 failed_items / 没有 status 轮询），失败只有一种形态：
    这次调用没拿到 task_id。所以判据就是 status，不必再看有没有明细。
    """
    return (rec.get("status") or "") == "failed"


def merge_split(parent, children):
    """把「整批提交失败 + 逐条重试的子任务」合成一条 group，对外仍是一组。"""
    merged = dict(parent)
    merged["split"] = True
    merged["children"] = children
    ids = [c["task_id"] for c in children if c.get("task_id")]
    merged["task_ids"] = ids
    merged["task_id"] = ids[0] if len(ids) == 1 else ""
    ok = [c for c in children if not is_task_level_failure(c)]
    merged["status"] = "submitted" if ok else "failed"
    # 全挂时保留父任务的错误原文（那是整批被拒的真实原因），否则父任务错误已无意义
    merged["error"] = "" if ok else (parent.get("error") or "")
    added = {}
    for c in children:
        added.update(c.get("added") or {})
    merged["added"] = added
    merged["reused"] = sum(int(c.get("reused") or 0) for c in children)
    return merged


def group_counts(rec):
    """一条 group 的四档计数。

    新接口只回 task_id，既没有条目级明细也没有状态查询，所以「成没成」只能靠
    **目标目录前后比对**得出：

        ok      目录里实际新增的条目数          ← 唯一硬指标
        reused  提交的链接里 name 已在目录中     ← 仅 config 写了 name 才判得出
        unknown 提交了但目录没新增              ← skip 去重 与「悄悄没入库」的混合体
        failed  提交阶段就失败

    `unknown` 故意不做二次猜测：猜错就会把「没入库」报成「已存在」，那是假成功。
    """
    n = rec["count"]
    if rec.get("children"):
        out = {"ok": 0, "reused": 0, "unknown": 0, "failed": 0}
        for child in rec["children"]:
            for k, v in group_counts(child).items():
                out[k] += v
        return out
    if is_task_level_failure(rec):
        return {"ok": 0, "reused": 0, "unknown": 0, "failed": n}
    ok = len(rec.get("added") or {})
    reused = min(int(rec.get("reused") or 0), n)
    return {"ok": ok, "reused": reused, "unknown": max(n - ok - reused, 0), "failed": 0}


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------

def cmd_list():
    if not os.path.isdir(PROFILES):
        print("还没有 profile。先跑 `sync.py --profile <名字> init`。")
        return 0
    names = sorted(d for d in os.listdir(PROFILES) if os.path.isdir(os.path.join(PROFILES, d)))
    if not names:
        print("还没有 profile。先跑 `sync.py --profile <名字> init`。")
        return 0
    for n in names:
        try:
            cfg = load_config(n)
            desc = "%d 任务 / %d 条 | %s | 凭证 %s" % (
                len(cfg["tasks"]), sum(len(t["files"]) for t in cfg["tasks"]),
                cfg["conflict_strategy"],
                "手填 " + api.mask(cfg["token"]) if cfg["token"] else "宿主连接器")
        except ConfigError as e:
            desc = "未就绪：%s" % str(e).splitlines()[0]
        print("  %-20s %s" % (n, desc))
    return 0


def cmd_resolve(profile, url):
    """乐享目录 / 知识库链接 → space_id + parent_entry_id。一次性转换，不进 run 主路径。"""
    creds = load_creds(profile)
    client = api.Client(creds["token"], creds["endpoint"], creds["timeout"])
    t = client.resolve_target(str(url or "").strip())
    print(json.dumps({"space_id": t["space_id"], "parent_entry_id": t["parent_entry_id"],
                      "name": t.get("name") or "", "entry_type": t.get("entry_type") or ""},
                     ensure_ascii=False, indent=2))
    print("把这两个 id 填进 config.json。")
    return 0


def _fetch_doc_markdown(client, url):
    """乐享文档链接 → (entry_id, markdown 正文)。"""
    entry_id, _space = api.parse_lexiang_url(str(url or "").strip())
    if not entry_id:
        raise ConfigError("需要一个乐享文档链接（…/pages/<32位id>），当前是「%s」"
                          % str(url or "")[:80])
    body = client.call("block_fetch_page", {"entry_id": entry_id, "render_mode": "markdown"})
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return entry_id, text


def _save_config(path, raw):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _apply_target(raw, client, target_url, log):
    """把目标目录链接解析成 space_id / parent_entry_id 写进 raw。"""
    t = client.resolve_target(str(target_url).strip())
    raw["space_id"] = t["space_id"]
    raw["parent_entry_id"] = t["parent_entry_id"]
    log("目标目录：%s（space=%s dir=%s）"
        % (t.get("name") or "-", t["space_id"], t["parent_entry_id"]))


def collect_links(profile, url, target_url=None, log=print):
    """读一篇乐享文档 → 抽链接 → 按源分组 → 写回 config.tasks。

    这是「优雅初始化」的主路径：不必手抄链接，也不必为每条链接手写一个 task 块。
    """
    creds = load_creds(profile)
    client = api.Client(creds["token"], creds["endpoint"], creds["timeout"])
    entry_id, text = _fetch_doc_markdown(client, url)
    links = extract_links(text)
    if not links:
        raise ConfigError("「%s」正文里没抽到任何 http(s) 链接" % entry_id)
    groups, unknown = group_links(links)

    path = os.path.join(PROFILES, profile, "config.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    with open(path + ".bak", "w", encoding="utf-8") as f:   # 覆盖前留一份，防手填内容被冲掉
        f.write(json.dumps(raw, ensure_ascii=False, indent=2))

    raw["tasks"] = build_tasks(links)
    if target_url:
        _apply_target(raw, client, target_url, log)
    _save_config(path, raw)

    log("源文档 %s → 抽到 %d 条链接，分成 %d 组：" % (entry_id, len(links), len(groups)))
    for t, urls in groups.items():
        log("  [%s] %s · %d 条" % (t, label_of(t), len(urls)))
    if unknown:
        log("  ⚠️ %d 条认不出源类型，未写入（手工确认后可加到 config 的任意 task）：" % len(unknown))
        for u in unknown:
            why = unsupported_reason(u)
            log("     " + u[:110] + ("  ← " + why if why else ""))
    log("已写入：%s" % path)
    return 0


def cmd_init(profile, from_url=None, target_url=None):
    os.makedirs(PROFILES, exist_ok=True)
    gi = os.path.join(PROFILES, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(GITIGNORE)
    os.makedirs(os.path.join(PROFILES, profile), exist_ok=True)
    path = os.path.join(PROFILES, profile, "config.json")
    if os.path.exists(path):
        print("已存在，未覆盖：%s" % path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(CONFIG_TEMPLATE)
        print("已生成：%s" % path)
        print("三步：① `resolve <乐享目录链接>` 查 space_id / parent_entry_id 并填入；"
              "② tasks 里填 type + 源端链接（同 type 收进同一个 task 的 files）；"
              "③ token（留空则用宿主连接器）。")
    if from_url:
        print("")
        collect_links(profile, from_url, target_url, log=print)
        print("")
        print("下一步：`plan` 核对 → `run --dry-run` 预演 → `run` 真跑。")
    elif target_url:
        # 只解析目标，不动 tasks —— 链接由人来贴/由 collect 抽
        creds = load_creds(profile)
        client = api.Client(creds["token"], creds["endpoint"], creds["timeout"])
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        _apply_target(raw, client, target_url, log=print)
        _save_config(path, raw)
        print("")
        print("目标已写入 %s。下一步：往 tasks[].files 填源端链接 → `plan` → `run --dry-run`。" % path)
    return 0


def cmd_collect(profile, url, target_url=None):
    collect_links(profile, url, target_url, log=print)
    print("")
    print("下一步：`plan` 核对（不联网）→ `run --dry-run` 预演。")
    return 0


def cmd_plan(cfg):
    """纯本地核对：任务怎么分组、每条链接长什么样。不联网、不提交。"""
    print("[%s] 目标：space_id=%s  parent_entry_id=%s" % (cfg["profile"], cfg["space_id"],
                                                          cfg["parent_entry_id"]))
    total, warn = 0, 0
    conflict = replace_conflict(cfg)
    if conflict:
        print("⚠️ " + conflict)
    for i, task in enumerate(cfg["tasks"], 1):
        t = task["type"]
        print("  %d. [%s] %s · %d 条" % (i, t, label_of(t), len(task["files"])))
        if t not in KNOWN_TYPES:
            print("       ⚠️ type=%s 不在已知取值里（%s）；接口大概率回 50090008「导入类型不支持」"
                  % (t, " / ".join(KNOWN_TYPES)))
        for f in task["files"]:
            print("     %s%s" % (f["id"], ("  · " + f["name"]) if f.get("name") else ""))
            why = unsupported_reason(f["id"])
            if why:
                print("       ⚠️ %s（这条大概率领失败，建议从 files 里删掉）" % why)
            gt = guess_type(f["id"])
            if gt and gt != t:
                warn += 1
                print("       ⚠️ 链接 host 看着像 %s，与 type=%s 不一致（仍按你填的提交）" % (gt, t))
        total += len(task["files"])
    print("合计 %d 个任务 / %d 条%s" % (len(cfg["tasks"]), total,
                                        "  |  ⚠️ %d 条 type 存疑" % warn if warn else ""))
    return 0


def cmd_run(cfg, wait_timeout, json_out, log, split=True, debug=None):
    # debug 必须传到 Client：否则 --debug 只会在 stderr 多一份日志，请求/回包一个字节都不记
    client = api.Client(cfg["token"], cfg["endpoint"], cfg["timeout"], debug=debug)

    conflict = replace_conflict(cfg)
    if conflict:
        log("拒绝执行：" + conflict)
        return 2

    log("[%s] 提交导入 → space=%s dir=%s" % (cfg["profile"], cfg["space_id"],
                                            cfg["parent_entry_id"]))

    # 提交前先拍一份目标目录快照 —— 这是唯一能判「进没进库」的基线（没有任务状态可查）
    try:
        baseline = list_children(client, cfg["parent_entry_id"])
    except api.ApiError as e:
        log("读目标目录失败，拿不到核对基线，先不提交（免得写了库却说不清）：%s" % e)
        return 1
    before = set(baseline)
    seen = dict(baseline)
    log("目标目录现有 %d 条，作为入库核对基线" % len(baseline))

    results = []
    for task in cfg["tasks"]:
        log("\n  [%s] %s：%d 条 …" % (task["type"], label_of(task["type"]), len(task["files"])))
        rec = submit(client, cfg, task)
        if rec["task_id"]:
            log("    task_id = %s" % rec["task_id"])

        if split and len(task["files"]) > 1 and is_task_level_failure(rec):
            # 新工具没有条目级明细，整批失败时唯一能定位坏链接的办法就是逐条重提
            log("    整批提交失败 → 拆成单条重试，避免坏链接连坐同组")
            children = []
            for f in task["files"]:
                child = submit(client, cfg, {"type": task["type"], "files": [f]})
                if not is_task_level_failure(child):
                    child["added"] = wait_new_entries(client, cfg["parent_entry_id"],
                                                     seen, wait_timeout, log)
                    seen.update(child["added"])
                bad = is_task_level_failure(child)
                why = child.get("error") or ""
                log("      %s %s%s" % ("❌" if bad else "✅", str(f.get("id"))[:70],
                                      ("  ← " + why[:100]) if why else ""))
                children.append(child)
            rec = merge_split(rec, children)
        elif not is_task_level_failure(rec):
            mark_reused(rec, seen.values(), task["files"])
            log("    等目录出现新条目 …")
            rec["added"] = wait_new_entries(client, cfg["parent_entry_id"],
                                           seen, wait_timeout, log)
            seen.update(rec["added"])
        results.append(rec)

    report = build_report(cfg, results)
    state = read_state(cfg)
    state["run"] = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target": {"space_id": cfg["space_id"], "parent_entry_id": cfg["parent_entry_id"]},
        "baseline": sorted(before),
        "task_ids": [i for r in results for i in (r.get("task_ids") or [])],
        "tasks": _state_tasks(results),
        "added": [{"id": k, "name": v} for r in results for k, v in (r.get("added") or {}).items()],
        "summary": report["totals"],
    }
    state["last"] = state["run"]
    write_state(cfg, state)

    print(json.dumps(report, ensure_ascii=False, indent=2) if json_out
          else render(report))
    t = report["totals"]
    if t["failed"]:
        return 1
    # 一条都没新增：要么全被 skip 去重，要么压根没入库。别报成功。
    return 1 if (t["ok"] == 0 and t["reused"] == 0 and t["unknown"] > 0) else 0


def _state_tasks(results):
    """state.json 里记真实 task_id：拆单后逐个记，`status` 命令给得出全貌。"""
    out = []
    for r in results:
        for c in (r.get("children") or [r]):
            if c.get("task_id"):
                out.append({"task_id": c["task_id"], "type": c["type"],
                            "status": c.get("status") or ""})
    return out


def build_report(cfg, results):
    totals = {"ok": 0, "reused": 0, "unknown": 0, "failed": 0}
    out = []
    for rec in results:
        counts = group_counts(rec)
        for k in totals:
            totals[k] += counts[k]
        out.append({
            "type": rec["type"],
            "label": label_of(rec["type"]),
            "task_id": rec["task_id"],
            "task_ids": rec.get("task_ids") or ([rec["task_id"]] if rec["task_id"] else []),
            "split": bool(rec.get("split")),
            "status": rec["status"] or "not_created",
            "count": rec["count"],
            "counts": counts,
            "inputs": rec.get("inputs") or [],
            "added": [{"id": k, "name": v} for k, v in (rec.get("added") or {}).items()],
            "error": rec.get("error") or "",
            "hints": hints_of(rec),
        })
    return {"profile": cfg["profile"],
            "target": {"space_id": cfg["space_id"], "parent_entry_id": cfg["parent_entry_id"]},
            "conflict_strategy": cfg["conflict_strategy"],
            "groups": out, "totals": totals}


def render(report):
    t = report["totals"]
    tgt = report["target"]
    lines = ["", "── 结果 ──",
             "目标    : space=%s dir=%s" % (tgt["space_id"], tgt["parent_entry_id"]),
             "策略    : %s%s" % (report["conflict_strategy"],
                                "" if report["conflict_strategy"] == "skip" else "（非安全档）")]
    for g in report["groups"]:
        c = g["counts"]
        lines.append("[%s] %s · %d 条 · task=%s · %s%s"
                     % (g["type"], g["label"], g["count"], g["task_id"] or "-",
                        g["status"],
                        "（整批被拒 → 已拆单逐条重试）" if g.get("split") else ""))
        lines.append("    ✅ 目录新增 %d  |  ♻️ 名字已存在 %d  |  ❓ 未新增 %d  |  ❌ 提交失败 %d"
                     % (c["ok"], c["reused"], c["unknown"], c["failed"]))
        for e in g["added"]:
            lines.append("    + %s  %s" % (e["id"], e["name"] or "(无标题)"))
        if g["error"]:
            lines.append("    ⚠️ %s" % g["error"][:300])
        for tip in g.get("hints") or []:
            lines.append("    💡 %s" % tip)
    lines.append("")
    lines.append("合计：✅ 新增 %d  |  ♻️ 已存在 %d  |  ❓ 未新增 %d  |  ❌ 失败 %d"
                 % (t["ok"], t["reused"], t["unknown"], t["failed"]))
    if t["unknown"]:
        lines.append("❓ 「未新增」= 提交成功但目标目录里没多出条目。可能是 skip 撞上已存在的文档，"
                     "也可能压根没入库 ——")
        lines.append("   接口没有状态可查，这条脚本判不了。跑 `status` 看目录实况，别直接重跑。")
    if t["ok"] == 0 and t["unknown"] and not t["failed"]:
        lines.append("⚠️ 一条都没新增。先 `status` 确认是不是本来就在目录里（skip 去重），"
                     "不是的话查乐享侧该源的授权。")
    return "\n".join(lines)


def cmd_status(cfg, log, debug=None):
    """复查上次运行后目标目录的变化。

    新接口没有任务状态查询（import_describe_task 已下线），所以这里不查任务，
    而是拿上次提交前的目录快照当基线，列出「现在比那时多了什么」。
    """
    state = read_state(cfg)
    run = state.get("last") or {}
    if not run:
        print("没有任务记录（先跑 `run`）。")
        return 0
    client = api.Client(cfg["token"], cfg["endpoint"], cfg["timeout"], debug=debug)
    print("上次运行：%s" % (run.get("at") or "-"))
    print("task_id：%s" % (", ".join(run.get("task_ids") or []) or "-"))
    try:
        now = list_children(client, cfg["parent_entry_id"])
    except api.ApiError as e:
        print("读目录失败：%s" % e)
        return 1
    baseline = set(run.get("baseline") or [])
    new = {k: v for k, v in now.items() if k not in baseline}
    print("目标目录现有 %d 条；相对上次运行新增 %d 条" % (len(now), len(new)))
    for k, v in new.items():
        print("  + %s  %s" % (k, v or "(无标题)"))
    if not new:
        print("  （没有新增条目。要么这些文档本来就在目录里被 skip 跳过，要么没入库 ——")
        print("    带上 --debug 重跑能拿到请求/回包原文。）")
        return 1
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="sync.py", description="多源在线文档 → 乐享 批量/增量导入")
    p.add_argument("--profile", default="default", help="profile 名（默认 default）")
    p.add_argument("--debug", action="store_true", help="把请求/回包写进 profiles/<name>/sync.log")
    sub = p.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="生成 config.json（已存在不覆盖）")
    init.add_argument("--from", dest="from_url", metavar="乐享文档链接",
                      help="顺带读这篇乐享文档、抽链接并按源分组写入 tasks")
    init.add_argument("--target", dest="target_url", metavar="乐享目录链接",
                      help="顺带解析目标目录，写入 space_id / parent_entry_id")
    sub.add_parser("list", help="列 profile")
    collect = sub.add_parser("collect", help="读乐享文档 → 抽链接 → 按源分组写回 config")
    collect.add_argument("url", help="乐享文档链接（…/pages/<32位id>）")
    collect.add_argument("--target", dest="target_url", metavar="乐享目录链接",
                         help="顺带解析目标目录，写入 space_id / parent_entry_id")
    sub.add_parser("plan", help="本地核对 tasks，不联网不提交")
    resolve = sub.add_parser("resolve", help="乐享目录链接 → space_id / parent_entry_id")
    resolve.add_argument("url", help="乐享目录或知识库链接")
    run = sub.add_parser("run", help="提交导入并核对目录")
    run.add_argument("--dry-run", action="store_true",
                     help="已废弃：新接口忽略 dry_run（传了也真写库），带上它会直接拒绝执行")
    run.add_argument("--json", action="store_true", help="结果以 JSON 输出到 stdout")
    run.add_argument("--no-split", action="store_true",
                     help="关闭「整批提交失败后拆单重试」（默认开启）")
    run.add_argument("--wait", type=int, default=180, metavar="秒",
                     help="每个任务等目录出现新条目的上限（默认 180，连续 8 秒无新增即提前结束）")
    sub.add_parser("status", help="复查上次提交后目标目录多了什么")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        log = Log(os.path.join(PROFILES, args.profile, "sync.log") if args.debug else None)
        try:
            return cmd_init(args.profile, args.from_url, args.target_url)
        except ConfigError as e:
            sys.stderr.write("配置错误：%s\n" % e)
            return 2
        except api.ApiError as e:
            sys.stderr.write("接口失败：%s\n" % e)
            return 1
    if args.cmd == "list":
        return cmd_list()

    log = Log(os.path.join(PROFILES, args.profile, "sync.log") if args.debug else None)
    try:
        if args.cmd == "resolve":
            return cmd_resolve(args.profile, args.url)
        if args.cmd == "collect":
            return cmd_collect(args.profile, args.url, args.target_url)
        cfg = load_config(args.profile)
        if args.cmd == "plan":
            return cmd_plan(cfg)
        if args.cmd == "status":
            return cmd_status(cfg, log, debug=log if args.debug else None)
        if args.dry_run:
            # 不是懒：新工具没有 dry_run 语义，传了照样写库。留个假预演比不留更危险。
            sys.stderr.write(
                "已拒绝：entry_import_connector 不支持预演（dry_run 被忽略，传了也会真写库）。\n"
                "要看会提交什么，用 `plan`（纯本地，不联网不提交）。\n")
            return 2
        return cmd_run(cfg, max(30, args.wait), args.json, log,
                       split=not args.no_split, debug=log if args.debug else None)
    except ConfigError as e:
        sys.stderr.write("配置错误：%s\n" % e)
        return 2
    except api.AuthError as e:
        sys.stderr.write("鉴权失败：%s\n" % e)
        return 1
    except api.ApiError as e:
        sys.stderr.write("接口失败：%s\n" % e)
        return 1
    except RuntimeError as e:
        sys.stderr.write("失败：%s\n" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
