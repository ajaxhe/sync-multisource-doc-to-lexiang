#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 离线自检 + 只读回归。

    python3 scripts/selftest.py            # 全离线，约 1 秒，不联网不写库
    python3 scripts/selftest.py --online   # 追加只读联网用例（需要有效凭证）

覆盖的是**最容易改坏**的地方：链接解析 / 结果归纳 / 调用路径回退 / 配置校验。
改动 scripts/*.py 后跑一遍。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import api      # noqa: E402
import sync     # noqa: E402

PY = sys.executable
CASES = []
ID = "a" * 32


def case(name):
    def wrap(fn):
        CASES.append((name, fn))
        return fn
    return wrap


def cli(args, timeout=90):
    p = subprocess.run([PY, os.path.join(HERE, "sync.py")] + args, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "ignore"), p.stderr.decode("utf-8", "ignore")


# --------------------------------------------------------------------------
# A. 传输层（api.py）
# --------------------------------------------------------------------------

@case("A1 parse_lexiang_url 认 pages / spaces / 裸 id")
def _():
    assert api.parse_lexiang_url("https://lexiangla.com/pages/%s?company_from=x" % ID) == (ID, None)
    assert api.parse_lexiang_url("https://lexiangla.com/spaces/%s" % ID) == (None, ID)
    assert api.parse_lexiang_url(ID) == (ID, None)
    assert api.parse_lexiang_url("https://lexiangla.com/ai/claw") == (None, None)


@case("A2 mask 不泄露完整 token")
def _():
    assert api.mask("") == "(空)"
    long_tok = "lxmcp_0123456789abcdef8888"
    out = api.mask(long_tok)
    assert "***" in out and long_tok not in out, out


@case("A3 _parse_sse 取最后一个能解析的帧")
def _():
    raw = 'event: message\ndata: {"a": 1}\n\ndata: {"b": 2}\n'
    assert api._parse_sse(raw) == {"b": 2}, api._parse_sse(raw)
    assert api._parse_sse("no frames") is None


@case("A4 路径被拒只认 tool not found / not allowed")
def _():
    assert api._tool_rejected("tool not found: foo")
    assert api._tool_rejected("Tool is not allowed: foo")
    # 业务错误不能被当成路径问题，否则会白白切路径并掩盖真实原因
    assert not api._tool_rejected("nodeNotExist")
    assert not api._tool_rejected("导入类型不支持")


@case("A5 _strip_entry_noise 只剃 entries 内的错误字段")
def _():
    payload = {"entries": [{"id": "1", "failed_reason": "x", "status": "failed"}],
               "failed_items": [{"id": "2", "failed_reason": "keep"}]}
    out = api._strip_entry_noise(payload)
    assert "failed_reason" not in out["entries"][0], out
    assert out["entries"][0]["status"] == "failed", out
    assert out["failed_items"][0]["failed_reason"] == "keep", out


@case("A6 直调被拒 → 自动切 call_tool 包装，并记住路径")
def _():
    calls = []

    class C(api.Client):
        def __init__(self):
            self.token, self.connector, self.headers = "t", None, {}
            self.endpoint, self.timeout, self.debug = "http://x", 5, None
            self._path, self._targets = {}, {}

        def _post(self, payload):
            name = payload["params"]["name"]
            calls.append(name)
            if name == "import_create_task":
                return {"error": {"code": -32601, "message": "tool not found: import_create_task"}}, "", False
            return {"result": {"content": [{"type": "text", "text": '{"code":0,"data":{}}'}]}}, "", False

    c = C()
    assert c.call("import_create_task", {"x": 1}) == {}
    assert calls == ["import_create_task", "call_tool"], calls
    assert c._path["import_create_task"] == "wrapped"
    c.call("import_create_task", {"x": 2})
    assert calls[-1] == "call_tool", "路径记忆失效，又试了直调"


@case("A7 业务错误码抛 ApiError，401 抛 AuthError")
def _():
    class C(api.Client):
        def __init__(self, text):
            self.token, self.connector, self.headers = "t", None, {}
            self.endpoint, self.timeout, self.debug = "http://x", 5, None
            self._path, self._targets, self.text = {}, {}, text

        def _post(self, payload):
            return {"result": {"content": [{"type": "text", "text": self.text}]}}, "", False

    try:
        C('{"code":50090008,"message":"导入类型不支持"}').call("t", {})
    except api.ApiError as e:
        assert "50090008" in str(e), e
    else:
        raise AssertionError("业务错误没有抛出")

    try:
        C('{"code":401,"message":"unauthorized"}').call("t", {})
    except api.AuthError:
        pass
    else:
        raise AssertionError("401 未归到 AuthError")


@case("A8 工具被拒的第三种写法也认；preset 缺失自动补")
def _():
    # 2026-11-11 服务端换了文案，漏认这串就会当正常返回值往上抛，上层拿 str 当 dict 用直接崩
    assert api._tool_rejected("tool not found or not allowed: import_create_task")
    assert api._tool_rejected("Tool is not allowed: x")
    assert not api._tool_rejected("腾讯文档导入失败")

    assert api.with_preset("http://h/mcp") == "http://h/mcp?preset=all"
    assert api.with_preset("http://h/mcp?a=1") == "http://h/mcp?a=1&preset=all"
    assert api.with_preset("http://h/mcp?preset=knowledge") == "http://h/mcp?preset=knowledge"
    assert api.with_preset("") == ""


# --------------------------------------------------------------------------
# B. 本地辅助（label / host 提示）
# --------------------------------------------------------------------------

@case("B1 guess_type 四个支持源的 host；未知返回 None")
def _():
    assert sync.guess_type("https://doc.weixin.qq.com/doc/w3_1") == "wecombot"
    assert sync.guess_type("https://drive.weixin.qq.com/s?k=1") == "wecombot"
    assert sync.guess_type("https://x.feishu.cn/wiki/abc") == "feishu"
    assert sync.guess_type("https://alidocs.dingtalk.com/i/nodes/x") == "dingtalk"
    assert sync.guess_type("https://docs.qq.com/doc/DU3BiQ0ZsVEVCcEVN") == "txdoc"
    assert sync.guess_type("https://docs.qq.com/sheet/DU3dGQ1J5SkZHa3J3") == "txdoc"
    # 腾讯文档企业版暂不支持：不能因为域名包含关系被误归成个人版
    assert sync.guess_type("https://saas.docs.qq.com/doc/abc") is None
    assert "企业版" in (sync.unsupported_reason("https://saas.docs.qq.com/doc/abc") or "")
    assert sync.unsupported_reason("https://docs.qq.com/doc/x") is None
    assert sync.guess_type("https://example.com/x") is None
    assert sync.guess_type("") is None


@case("B2 label_of：已知类型给中文名，未知原样返回")
def _():
    assert sync.label_of("wecombot") == "企业微信"
    assert sync.label_of("feishu") == "飞书"
    assert sync.label_of("txdoc") == "腾讯文档个人版"
    assert sync.label_of("markdown") == "Markdown"
    assert sync.label_of("whatever") == "whatever"


@case("B3 hints_of 只对具体上下文给建议；裸 ReAuthError 不误导去重新授权")
def _():
    tx = {"status": "failed", "error": "", "data": {"err_message": "腾讯文档授权已失效，请重新授权"}}
    tips = sync.hints_of(tx)
    assert tips and "重新授权" in tips[0], tips

    # 同一个授权问题还有工作流栈的写法：短文案之外必须也认这个，否则提示为空
    tx_stack = {"status": "failed", "error": "", "data": {"err_message":
        "activity error (type: ExecuteNextBatch, scheduledEventID: 5): list child nodes: "
        "腾讯文档导入失败，请稍后重试 (type: wrapError, retryable: true): "
        "腾讯文档导入失败，请稍后重试 (type: ReAuthError, retryable: true)"}}
    assert sync.hints_of(tx_stack), "工作流栈写法的授权失败没被识别"

    # 拆单后子任务里的原因也要能被识别
    split = {"status": "failed", "error": "", "data": {"err_message": ""},
             "children": [{"status": "failed", "error": "",
                           "data": {"err_message": "腾讯文档授权已失效，请重新授权"}}]}
    assert sync.hints_of(split), "拆单后的子任务原因没被看到"

    aio = {"status": "failed", "error": "", "data": {"err_message": "source selections are not importable"}}
    assert any("/doc/" in t for t in sync.hints_of(aio)), sync.hints_of(aio)

    # 网关没注册导入工具：得提示「别在本地瞎改」，否则看起来像配置问题
    gone = {"status": "", "error": "乐享 MCP 拒绝了两条调用路径（直调与 call_tool 包装）："
                                   "tool not found or not allowed: import_create_task", "data": {}}
    assert any("网关" in t for t in sync.hints_of(gone)), sync.hints_of(gone)

    # 企微单篇文档权限也会报 ReAuthError —— 这种情况不该劝人去重新授权连接器
    wecom = {"status": "failed", "error": "",
             "data": {"err_message": "activity error: list child nodes: ... (type: ReAuthError)"}}
    assert sync.hints_of(wecom) == [], sync.hints_of(wecom)
    assert sync.hints_of({"status": "succeed", "error": "", "data": {}}) == []


# --------------------------------------------------------------------------
# C. 配置校验（每次都是用法错误 → 退出码 2）
# --------------------------------------------------------------------------

CFG_DIR = os.path.join(sync.PROFILES, "_selftest_cfg")


def _cfg(payload):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(os.path.join(CFG_DIR, "config.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _expect_error(payload, keyword):
    _cfg(payload)
    try:
        sync.load_config("_selftest_cfg")
    except sync.ConfigError as e:
        assert keyword in str(e), "报错里没有「%s」：%s" % (keyword, e)
    else:
        raise AssertionError("没报错，配置写错会静默失效：%r" % payload)


@case("C1 合法配置：tasks / files 字段原样保留，可透传")
def _():
    _cfg({
        "token": "", "space_id": ID, "parent_entry_id": ID,
        "conflict_strategy": "skip",
        "tasks": [
            {"type": "wecombot", "files": [
                {"id": "https://doc.weixin.qq.com/doc/w3_1?scode=x"},
                {"id": "https://doc.weixin.qq.com/doc/w3_2?scode=y", "name": "标题"},
            ]},
            {"type": "feishu", "files": [
                {"id": "https://x.feishu.cn/wiki/a", "include_subpages": True},
            ]},
        ],
        "_comment": "随便写",
    })
    cfg = sync.load_config("_selftest_cfg")
    assert cfg["space_id"] == ID and cfg["token"] == "", cfg
    f0 = cfg["tasks"][0]["files"][0]
    assert f0 == {"id": "https://doc.weixin.qq.com/doc/w3_1?scode=x"}, f0
    f1 = cfg["tasks"][0]["files"][1]
    assert f1 == {"id": "https://doc.weixin.qq.com/doc/w3_2?scode=y", "name": "标题"}, f1
    f2 = cfg["tasks"][1]["files"][0]
    assert f2 == {"id": "https://x.feishu.cn/wiki/a", "include_subpages": True}, f2


@case("C2 id 字段：占位符 / 链接 / 缺失都要报错")
def _():
    good = {"space_id": ID, "parent_entry_id": ID, "tasks": [{"type": "t", "files": [{"id": "u"}]}]}
    _expect_error(dict(good, space_id="<32位目标知识库 id>"), "space_id")
    _expect_error(dict(good, parent_entry_id="https://lexiangla.com/pages/" + ID), "resolve")
    _expect_error({k: v for k, v in good.items() if k != "parent_entry_id"}, "parent_entry_id")


@case("C3 tasks 校验：空 / 缺 type / 缺 files / files 元素不是对象 / 缺 id / 未知字段")
def _():
    base = {"space_id": ID, "parent_entry_id": ID}
    _expect_error(dict(base, tasks=[]), "tasks")
    _expect_error(dict(base, tasks=[{"files": [{"id": "u"}]}]), "type")
    _expect_error(dict(base, tasks=[{"type": "feishu"}]), "files")
    _expect_error(dict(base, tasks=[{"type": "feishu", "files": ["https://x.feishu.cn/a"]}]), "files[0]")
    _expect_error(dict(base, tasks=[{"type": "feishu", "files": [{"name": "没 id"}]}]), "id")
    _expect_error(dict(base, tasks=[{"type": "feishu", "files": [{"id": "u", "typos": 1}]}]), "typos")
    _expect_error(dict(base, tasks=[{"type": "feishu", "file": []}]), "tasks[0]")


@case("C4 顶层未知字段 / 非法 conflict_strategy")
def _():
    good = {"space_id": ID, "parent_entry_id": ID, "tasks": [{"type": "t", "files": [{"id": "u"}]}]}
    _expect_error(dict(good, sources=["x"]), "sources")
    _expect_error(dict(good, batch_size=1), "batch_size")
    _expect_error(dict(good, conflict_strategy="overwrite"), "conflict_strategy")


@case("C5 replace 冲突：多任务即拒，单任务 / skip 不拒")
def _():
    two = {"conflict_strategy": "replace",
           "tasks": [{"type": "feishu", "files": [{"id": "u1"}]},
                     {"type": "wecombot", "files": [{"id": "u2"}]}]}
    assert sync.replace_conflict(two), "多任务 + replace 应报冲突"
    one = {"conflict_strategy": "replace", "tasks": [{"type": "feishu", "files": [{"id": "u"}]}]}
    assert sync.replace_conflict(one) is None
    skip = dict(two, conflict_strategy="skip")
    assert sync.replace_conflict(skip) is None
    dup = {"conflict_strategy": "replace",
           "tasks": [{"type": "feishu", "files": [{"id": "u1"}]},
                     {"type": "feishu", "files": [{"id": "u2"}]}]}
    assert "同 type 多任务" in sync.replace_conflict(dup)


@case("C6 load_creds 不要求 tasks 填好（resolve 命令用）")
def _():
    _cfg({"token": "lxmcp_x", "endpoint": "", "timeout": 5})
    creds = sync.load_creds("_selftest_cfg")
    assert creds["token"] == "lxmcp_x" and creds["timeout"] == 5, creds
    assert creds["endpoint"] == api.DEFAULT_ENDPOINT, "endpoint 缺省没兜底"


# --------------------------------------------------------------------------
# D. 结果归纳
# --------------------------------------------------------------------------

@case("D1 group_counts：目录新增是唯一硬指标，判不出的一律算 unknown")
def _():
    G = sync.group_counts
    hit = {"type": "txdoc", "count": 2, "status": "submitted", "added": {"a": "A", "b": "B"}}
    assert G(hit) == {"ok": 2, "reused": 0, "unknown": 0, "failed": 0}, G(hit)

    # 提交成功但目录没新增、config 又没写 name → 分不清「已存在被 skip」还是「没入库」
    blind = {"type": "txdoc", "count": 2, "status": "submitted", "added": {}}
    assert G(blind) == {"ok": 0, "reused": 0, "unknown": 2, "failed": 0}, G(blind)

    # 写了 name 且目录里已有同名条目 → 才敢判 reused（猜错就会把失败报成成功）
    assert G(dict(blind, reused=2)) == {"ok": 0, "reused": 2, "unknown": 0, "failed": 0}

    # 新增 + 命中并存时不重复计数
    mixed = {"type": "txdoc", "count": 2, "status": "submitted", "added": {"a": "A"}, "reused": 1}
    assert G(mixed) == {"ok": 1, "reused": 1, "unknown": 0, "failed": 0}, G(mixed)

    fail = {"type": "txdoc", "count": 2, "status": "failed", "error": "boom"}
    assert G(fail) == {"ok": 0, "reused": 0, "unknown": 0, "failed": 2}, G(fail)


@case("D2 mark_reused：config 没写 name 就不猜「已存在」")
def _():
    rec = {"reused": 0}
    sync.mark_reused(rec, ["标题A", "标题B", ""], [{"id": "u1"}, {"id": "u2", "name": "标题A"}])
    assert rec["reused"] == 1, rec
    rec2 = {"reused": 0}
    sync.mark_reused(rec2, ["标题A"], [{"id": "u1"}])
    assert rec2["reused"] == 0, rec2


@case("D3 is_task_level_failure：只认「这次没拿到 task_id」")
def _():
    assert sync.is_task_level_failure({"status": "failed", "error": "50090008"})
    assert not sync.is_task_level_failure({"status": "submitted", "task_id": "t"})


@case("D4 拆单兜底：整批提交失败 → 逐条重试，坏链接只毁自己")
def _():
    parent = {"type": "txdoc", "count": 3, "task_id": "", "status": "failed",
              "error": "整批被拒", "inputs": [{"id": "a"}], "added": {}}
    good = {"type": "txdoc", "count": 1, "task_id": "c1", "status": "submitted",
            "error": "", "added": {"x": "X"}, "reused": 0}
    bad = {"type": "txdoc", "count": 1, "task_id": "", "status": "failed",
           "error": "source selections are not importable", "added": {}}

    merged = sync.merge_split(parent, [good, bad])
    assert merged["split"] and merged["task_ids"] == ["c1"], merged["task_ids"]
    assert merged["status"] == "submitted", merged["status"]
    assert sync.group_counts(merged) == {"ok": 1, "reused": 0, "unknown": 0, "failed": 1}
    assert sync.hints_of(merged), "aio 形态的失败没给提示"

    # 全挂 → 保留整批被拒的原始错误（那才是真实原因）；失败数按逐条重试的结果计
    all_bad = sync.merge_split(parent, [bad, dict(bad), dict(bad)])
    assert all_bad["status"] == "failed" and all_bad["error"] == "整批被拒", all_bad
    assert sync.group_counts(all_bad) == {"ok": 0, "reused": 0, "unknown": 0, "failed": 3}, \
        sync.group_counts(all_bad)


@case("D5 build_report / render：一条都没新增必须显式警告")
def _():
    cfg = {"profile": "p", "space_id": ID, "parent_entry_id": ID, "conflict_strategy": "skip"}
    rec = {"type": "txdoc", "count": 2, "task_id": "t", "status": "submitted",
           "inputs": [{"id": "u"}], "added": {}, "reused": 0, "error": ""}
    rep = sync.build_report(cfg, [rec])
    assert rep["totals"] == {"ok": 0, "reused": 0, "unknown": 2, "failed": 0}, rep["totals"]
    text = sync.render(rep)
    assert "未新增" in text and "status" in text, text

    rep2 = sync.build_report(cfg, [dict(rec, added={"e1": "标题"})])
    assert rep2["totals"]["ok"] == 1, rep2["totals"]
    assert "标题" in sync.render(rep2), sync.render(rep2)


@case("D6 抽链接 + 按源分组：同 type 收进一个 task")
def _():
    md = ("清单：\n"
          "1. [企微 doc](https://doc.weixin.qq.com/doc/w3_aaa?scode=1)\n"
          "2. [企微 sheet](https://doc.weixin.qq.com/sheet/e3_bbb?scode=2)\n"
          "3. [飞书 docx](https://x.feishu.cn/docx/ccc)\n"
          "4. [钉钉 node](https://alidocs.dingtalk.com/i/nodes/ddd)\n"
          "重复一条：https://doc.weixin.qq.com/doc/w3_aaa?scode=1\n"
          "尾标点：https://doc.weixin.qq.com/doc/w3_eee?scode=5，\n"
          "未知源：https://example.com/x\n")
    links = sync.extract_links(md)
    assert "https://doc.weixin.qq.com/doc/w3_eee?scode=5" in links, links   # 尾部「，」被剥掉
    assert links.count("https://doc.weixin.qq.com/doc/w3_aaa?scode=1") == 1, "没去重"
    assert "https://example.com/x" in links

    groups, unknown = sync.group_links(links)
    assert list(groups) == ["wecombot", "feishu", "dingtalk"], list(groups)
    assert len(groups["wecombot"]) == 3, groups["wecombot"]
    assert len(groups["feishu"]) == 1 and len(groups["dingtalk"]) == 1
    assert unknown == ["https://example.com/x"], unknown

    tasks = sync.build_tasks(links)
    assert len(tasks) == 3, tasks          # 一个源一个 task，不是一条链接一个 task
    assert sum(len(t["files"]) for t in tasks) == 5, tasks
    assert tasks[0]["type"] == "wecombot" and len(tasks[0]["files"]) == 3
    # 分组结果必须能过配置校验
    assert len(sync._check_tasks(tasks)) == 3


@case("D7 腾讯文档链接自动归到 txdoc；报告带 💡 提示")
def _():
    md = ("腾讯文档清单：\n"
          "1. https://docs.qq.com/doc/DU3BiQ0ZsVEVCcEVN\n"
          "2. https://docs.qq.com/sheet/DU3dGQ1J5SkZHa3J3\n"
          "3. https://docs.qq.com/aio/DU3VFVXpEVmVveGFE\n")
    links = sync.extract_links(md)
    groups, unknown = sync.group_links(links)
    assert list(groups) == ["txdoc"], list(groups)
    assert len(groups["txdoc"]) == 3 and unknown == [], (groups, unknown)
    tasks = sync.build_tasks(links)
    assert tasks[0]["type"] == "txdoc" and len(tasks[0]["files"]) == 3, tasks
    assert sync._check_tasks(tasks)[0]["type"] == "txdoc"

    rec = {"type": "txdoc", "count": 2, "task_id": "t", "status": "failed",
           "error": "腾讯文档授权已失效，请重新授权", "inputs": [], "added": {}}
    rep = sync.build_report({"profile": "p", "space_id": ID, "parent_entry_id": ID,
                             "conflict_strategy": "skip"}, [rec])
    assert rep["groups"][0]["hints"], rep["groups"][0]
    assert "💡" in sync.render(rep), sync.render(rep)


@case("D8 import_args：源类型必须是顶层 oneof 字段，不能是 type/files")
def _():
    cfg = {"space_id": ID, "parent_entry_id": ID, "conflict_strategy": "skip"}
    files = [{"id": "https://docs.qq.com/doc/DU0ZBTFdGc2RmYUNq"}]
    args = sync.import_args(cfg, {"type": "txdoc", "files": files})
    # 写成 {"type":"txdoc","files":[…]} 只会回一句 oneof 报错，看不出所以然，
    # 所以这里把形状钉死：源类型就是字段名。
    assert set(args) == {"space_id", "parent_entry_id", "conflict_strategy", "txdoc"}, args
    assert args["txdoc"] == {"files": files}, args
    assert args["conflict_strategy"] == "skip"
    assert "dry_run" not in args, "新工具忽略 dry_run，传了会误导人以为没写库"


@case("D9 wait_new_entries：新增稳定后收尾，不会一直等到上限")
def _():
    pages = {"n": 0}

    def fake_list(client, parent_id, limit=100, max_pages=20):
        pages["n"] += 1
        return {"a": "A", "b": "B"} if pages["n"] >= 2 else {}

    orig = sync.list_children
    sync.list_children = fake_list
    try:
        t0 = time.time()
        added = sync.wait_new_entries(None, "p", {}, 60, lambda *_: None, quiet=2, interval=0.2)
        cost = time.time() - t0
    finally:
        sync.list_children = orig
    assert added == {"a": "A", "b": "B"}, added
    # 曾经的 bug：拿「fresh 非空」当变化 → 每轮刷新计时器 → 一直等到 --wait（180s）
    assert cost < 6, "新增稳定后仍等满了上限：%.1fs" % cost


# --------------------------------------------------------------------------
# E. CLI
# --------------------------------------------------------------------------

@case("E1 init 生成模板且不覆盖；模板能被认出「还没填」")
def _():
    name = "_selftest_init"
    rc, out, _ = cli(["--profile", name, "init"])
    assert rc == 0 and "已生成" in out, (rc, out)
    path = os.path.join(sync.PROFILES, name, "config.json")
    first = open(path, encoding="utf-8").read()
    rc, out, _ = cli(["--profile", name, "init"])
    assert rc == 0 and open(path, encoding="utf-8").read() == first, "init 覆盖了已有配置"
    # 生成的模板必须能被 load_config 认出「还没填」而不是崩
    try:
        sync.load_config(name)
    except sync.ConfigError as e:
        assert "space_id" in str(e), e
    else:
        raise AssertionError("模板占位符没被识别")
    shutil.rmtree(os.path.join(sync.PROFILES, name))


@case("E2 未知命令 → 2；不存在的 profile → 2；resolve 缺 url → 2")
def _():
    rc, _, _ = cli(["no-such-cmd"])
    assert rc == 2, rc
    rc, _, err = cli(["--profile", "__nope__", "plan"])
    assert rc == 2 and "不存在" in err, (rc, err[:200])
    rc, _, _ = cli(["--profile", "default", "resolve"])
    assert rc == 2, rc


@case("E3 list 可跑，不打印完整 token")
def _():
    rc, out, _ = cli(["list"])
    assert rc == 0, rc
    try:
        tok = sync.load_creds("default")["token"]
    except sync.ConfigError:
        return
    assert tok not in out, "list 输出了完整 token"
    if tok:
        assert "***" in out, "token 未打码"


@case("E4 plan 纯本地：显示分组 + type 存疑提示（用临时 profile）")
def _():
    name = "_selftest_plan"
    d = os.path.join(sync.PROFILES, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"token": "", "space_id": ID, "parent_entry_id": ID,
                   "conflict_strategy": "skip",
                   "tasks": [{"type": "wecombot", "files": [
                       {"id": "https://doc.weixin.qq.com/doc/w3_1?scode=x"},
                       {"id": "https://x.feishu.cn/wiki/a"},
                   ]}]}, f, ensure_ascii=False)
    rc, out, _ = cli(["--profile", name, "plan"])
    assert rc == 0, (rc, out)
    assert "1 个任务 / 2 条" in out, out
    assert "type 存疑" in out and "feishu" in out, out  # 企微任务里混了飞书链接要提示
    shutil.rmtree(d)


@case("E5 run --dry-run 必须被拒绝（新接口忽略 dry_run，会真写库）")
def _():
    name = "_selftest_dry"
    d = os.path.join(sync.PROFILES, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"token": "", "space_id": ID, "parent_entry_id": ID,
                   "conflict_strategy": "skip",
                   "tasks": [{"type": "txdoc", "files": [{"id": "https://docs.qq.com/doc/x"}]}]},
                  f, ensure_ascii=False)
    rc, out, err = cli(["--profile", name, "run", "--dry-run"], timeout=60)
    assert rc == 2, (rc, out, err)
    assert "不支持预演" in err and "plan" in err, err
    # 拒绝发生在任何网络调用之前：不能先提交再报错
    assert "提交导入" not in out, out
    shutil.rmtree(d)


# --------------------------------------------------------------------------
# F. 只读联网
# --------------------------------------------------------------------------

@case("F1 [online] txdoc-test：目录可读 + 提交入参形状正确")
def _():
    if not os.path.isdir(os.path.join(sync.PROFILES, "txdoc-test")):
        print("        （txdoc-test 不存在，按 references/test-links.md 配好后可跑）")
        return
    cfg = sync.load_config("txdoc-test")
    client = api.Client(cfg["token"], cfg["endpoint"], cfg["timeout"])
    entries = sync.list_children(client, cfg["parent_entry_id"])
    assert isinstance(entries, dict), entries
    print("        目标目录现有 %d 条，新增核对基线可用" % len(entries))
    for t in cfg["tasks"]:
        args = sync.import_args(cfg, t)
        assert t["type"] in args, args
        assert set(args) == {"space_id", "parent_entry_id", "conflict_strategy", t["type"]}, args


def main():
    online = "--online" in sys.argv
    passed = failed = skipped = 0
    for name, fn in CASES:
        if "[online]" in name and not online:
            print("SKIP  %s" % name)
            skipped += 1
            continue
        try:
            fn()
            print("PASS  %s" % name)
            passed += 1
        except AssertionError as e:
            print("FAIL  %s\n        %s" % (name, str(e)[:400]))
            failed += 1
        except Exception as e:
            print("ERROR %s\n        %s: %s" % (name, type(e).__name__, str(e)[:400]))
            failed += 1
    if os.path.isdir(CFG_DIR):
        shutil.rmtree(CFG_DIR, ignore_errors=True)
    print("\n%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
