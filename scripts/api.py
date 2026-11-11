#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api.py —— 乐享 MCP 传输层（凭证 / JSON-RPC / 调用路径 / 目标解析）。

只有这一层知道 HTTP 与 JSON-RPC 的存在；上层只看到「调一个工具，拿到 data」。

调用路径（不写死，按被拒情况自适应）
    endpoint 带 `?preset=all` 时业务工具可直接调用（实测 2026-10-20）。但可见工具集由服务端按
    company / feature_flag / allowlist 过滤，同一份代码在不同租户下可能只剩元工具。
    因此：**先试直调 → 被拒再走 `call_tool` 包装**，成功过的路径记忆在本进程内，
    后续调用直接用。被拒的特征是 JSON-RPC error 或纯文本里的 `tool not found:` /
    `tool is not allowed:` —— 只在这两种情况下切路径，不拿别的原因当借口重试。

重试
    只重试「请求没被服务端处理」的情况（连接被拒 / 408 / 429 / 5xx）。超时与连接重置
    一律不重试：`import_create_task` 不是幂等的，重发可能建出两个任务。

凭证（**配置优先**）
    ① config 的 `token` 非空 → 直连 `endpoint`；
    ② 留空 → 从宿主注入的 `CODEBUDDY_MCP_CONFIG` 里找名字含 lexiang 的连接器条目，
       用它的 url + 认证头（会话级，不落盘）；
    ③ 都没有 → 报错退出，不降级。
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://mcp.lexiang-app.com/mcp?preset=all"
TOKEN_URL = "https://lexiangla.com/ai/claw"
LEXIANG_ORIGIN = os.environ.get("LEXIANG_ORIGIN", "https://lexiangla.com").rstrip("/")

META_TOOLS = frozenset(("whoami", "call_tool", "get_tool_schema", "lexiang_fetch",
                        "lexiang_search", "list_tool_categories", "search_tools"))
CALL_TOOL = "call_tool"
CONNECTOR_ENV = "CODEBUDDY_MCP_CONFIG"
CONNECTOR_AUTH_KEYS = ("authorization", "x-workbuddy-mcp-context")

IN_PROGRESS = frozenset(("processing", "page_processing"))
FINAL = frozenset(("succeed", "failed", "canceled"))

RETRY_HTTP = frozenset((408, 429, 500, 502, 503, 504))
ATTEMPTS = 3

_PAGE_RX = re.compile(r"/pages/([0-9a-fA-F]{32})")
_SPACE_RX = re.compile(r"/spaces/([0-9a-fA-F]{32})")
_BARE_RX = re.compile(r"^[0-9a-fA-F]{32}$")
# 条目级错误字段：`failed_items[]` 已经逐条给出，entries 里再带一份只会让模型重复引用
_ENTRY_NOISE = frozenset(("failed_reason", "failed_code", "err_message", "error_detail"))


class ApiError(RuntimeError):
    """接口返回了明确的业务错误（`code != 0`）或传输失败。"""


class AuthError(ApiError):
    """凭证无效 / 过期 / 无权限（HTTP 401/403 或业务回包里的 unauthorized）。"""


class _PathRejected(Exception):
    """服务端拒绝了**当前调用路径**（不是工具本身），用于直调 ⇄ 包装之间回退。"""


# --------------------------------------------------------------------------
# 链接解析
# --------------------------------------------------------------------------

def parse_lexiang_url(url):
    """乐享链接 → (entry_id, space_id)。裸 32 位 id 视为 entry_id。"""
    s = (url or "").strip()
    page = _PAGE_RX.search(s)
    space = _SPACE_RX.search(s)
    entry = page.group(1) if page else None
    sid = space.group(1) if space else None
    if not entry and not sid and _BARE_RX.match(s):
        entry = s
    return entry, sid


def mask(token):
    if not token:
        return "(空)"
    return token[:10] + "***" + token[-4:] if len(token) > 14 else token[:4] + "***"


# --------------------------------------------------------------------------
# 凭证
# --------------------------------------------------------------------------

def with_preset(url):
    """endpoint 缺 `preset=all` 时补上。

    实测：不带 preset 时 `tools/list` 只回 7 个元工具，`import_create_task` 直呼是
    `tool not found`、走 `call_tool` 包装是 `tool not found or not allowed` —— 看起来像
    「接口没了」，其实只是 preset 丢了。宿主连接器的 url 会变（2026-11-11 端口从 65336
    变 58874 且丢了 preset），所以这里兜底，不依赖宿主配置正确。
    """
    u = str(url or "").strip()
    if not u or "preset=" in u:
        return u
    return u + ("&" if "?" in u else "?") + "preset=all"


def discover_connector():
    """从宿主注入的连接器配置里找乐享凭证；找不到返回 None。会话级，绝不落盘。"""
    raw = (os.environ.get(CONNECTOR_ENV) or "").strip()
    if not raw:
        return None
    try:
        servers = (json.loads(raw) or {}).get("mcpServers") or {}
    except (ValueError, AttributeError):
        return None
    for name, sc in servers.items():
        if "lexiang" not in str(name).lower() or not isinstance(sc, dict):
            continue
        url = str(sc.get("url") or "").strip()
        headers = {str(k): str(v) for k, v in (sc.get("headers") or {}).items()
                   if str(k).lower() in CONNECTOR_AUTH_KEYS and v}
        if url.startswith("http") and any(k.lower() == "authorization" for k in headers):
            return {"endpoint": with_preset(url), "headers": headers}
    return None


# --------------------------------------------------------------------------
# 报文处理
# --------------------------------------------------------------------------

def _strip_entry_noise(node, in_entry=False):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if in_entry and k in _ENTRY_NOISE:
                continue
            out[k] = ([_strip_entry_noise(e, True) for e in v]
                      if k == "entries" and isinstance(v, list)
                      else _strip_entry_noise(v, in_entry))
        return out
    if isinstance(node, list):
        return [_strip_entry_noise(x, in_entry) for x in node]
    return node


def _parse_sse(raw):
    """MCP 也允许 `text/event-stream`：取最后一个能解析的 data: 帧。"""
    found = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                found = json.loads(line[5:].strip())
            except ValueError:
                continue
    return found


def _tool_rejected(text):
    """服务端拒绝了「调用路径/可见性」，不是业务失败。

    ⚠️ 形态会变，实测至少三种（同一天可能不同）：
        `tool not found: import_create_task`
        `tool is not allowed: import_create_task`
        `tool not found or not allowed: import_create_task`   ← 2026-11-11 新增
    漏掉任一种，这串文本就会被当成正常返回值往上抛，上层再拿它当 dict 用就崩。
    """
    low = str(text or "").strip().lower()
    return any(low.startswith(m) for m in ("tool not found:",
                                           "tool is not allowed:",
                                           "tool not found or not allowed:"))


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------

class Client(object):
    """一个 profile 的会话：凭证 + endpoint + 路径记忆 + 目标解析缓存。"""

    def __init__(self, token="", endpoint="", timeout=30, debug=None):
        self.token = str(token or "").strip()
        self.connector = None
        if self.token:
            self.endpoint = with_preset(endpoint or DEFAULT_ENDPOINT)
            self.headers = {}
        else:
            self.connector = discover_connector()
            if not self.connector:
                raise ApiError(
                    "缺少乐享 MCP 凭证，二选一：\n"
                    "  ① config.json 的 token 填个人 MCP Token（%s），适合定时任务；\n"
                    "  ② token 留空，并在 WorkBuddy 里连接「乐享知识库」连接器，脚本自动取会话凭证。"
                    % TOKEN_URL)
            self.endpoint = self.connector["endpoint"]
            self.headers = dict(self.connector["headers"])
        self.timeout = max(5, int(timeout or 30))
        self.debug = debug
        self._path = {}        # tool -> "direct" | "wrapped"
        self._targets = {}     # url -> {space_id, parent_entry_id}

    # -- 传输 ---------------------------------------------------------
    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False)
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        headers.update(self.headers)
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.debug:
            self.debug(">>> " + body)
        req = urllib.request.Request(self.endpoint, data=body.encode("utf-8"),
                                     headers=headers, method="POST")
        last = None
        for attempt in range(ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                break
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "ignore")[:400]
                if e.code in (401, 403):
                    raise AuthError("乐享鉴权失败（HTTP %s）：%s\n  %s"
                                    % (e.code, text[:200], self._auth_hint()))
                if e.code not in RETRY_HTTP or attempt == ATTEMPTS - 1:
                    raise ApiError("调用乐享 MCP 失败：HTTP %s %s" % (e.code, text))
                last = "HTTP %s" % e.code
            except urllib.error.URLError as e:
                # 连接被拒 = 请求肯定没到服务端，可安全重试；超时 / 重置 = 可能已处理，不重试
                if "refused" not in str(e.reason).lower() or attempt == ATTEMPTS - 1:
                    raise ApiError("连不上乐享 MCP（%s）：%s" % (self.endpoint, e.reason))
                last = str(e.reason)
            except (TimeoutError, OSError) as e:
                raise ApiError("调用乐享 MCP 超时或连接中断（%s）。任务可能已创建，"
                               "先用 `status` 核对再重跑：%s" % (self.endpoint, e))
            if self.debug:
                self.debug("... 重试（%s）" % last)
            time.sleep(1.5 * (attempt + 1))
        else:
            raise ApiError("调用乐享 MCP 失败：重试 %d 次仍不通（%s）" % (ATTEMPTS, last))

        if self.debug:
            self.debug("<<< " + raw[:8000])
        try:
            outer = json.loads(raw) if raw.lstrip().startswith("{") else _parse_sse(raw)
        except ValueError:
            outer = None
        if not isinstance(outer, dict):
            raise ApiError("接口回包不是 JSON（用 --debug 看 sync.log）：%s" % raw[:300])
        return outer, raw

    def _dispatch(self, name, arguments):
        """按记忆路径调用；被拒则切换另一条路径。"""
        if name in META_TOOLS:
            return self._unwrap(self._post(self._rpc(name, arguments))[0])
        first = self._path.get(name)
        order = ["wrapped", "direct"] if first == "wrapped" else ["direct", "wrapped"]
        rejected = None
        for path in order:
            payload = (self._rpc(CALL_TOOL, {"tool_name": name, "arguments": arguments})
                       if path == "wrapped" else self._rpc(name, arguments))
            try:
                result = self._unwrap(self._post(payload)[0])
            except _PathRejected as e:
                rejected = e
                if self.debug:
                    self.debug("--- 路径 %s 被拒：%s" % (path, e))
                continue
            self._path[name] = path
            return result
        raise ApiError("乐享 MCP 拒绝了两条调用路径（直调与 call_tool 包装）：%s" % rejected)

    @staticmethod
    def _rpc(name, arguments):
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    def _unwrap(self, outer):
        err = outer.get("error") if isinstance(outer, dict) else None
        if err:
            msg = str(err.get("message") if isinstance(err, dict) else err)
            if _tool_rejected(msg):
                raise _PathRejected(msg)
            raise ApiError("乐享 MCP 返回错误：%s" % msg[:300])
        result = (outer or {}).get("result") or {}
        for item in result.get("content") or []:
            if not (isinstance(item, dict) and item.get("type") == "text"):
                continue
            text = str(item.get("text") or "")
            try:
                return json.loads(text)
            except ValueError:
                if _tool_rejected(text):
                    raise _PathRejected(text.strip()[:160])
                # 有的接口正常也回纯文本（`block_fetch_page` 的正文、`block_update_page` 的成功语）。
                # 只要不是「路径被拒」，就当结果原样交回上层 —— 判错只会把成功误报成失败。
                return text
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        raise ApiError("无法解析接口回包（用 --debug 看 sync.log）")

    def _auth_hint(self):
        if self.connector:
            return ("当前凭证取自宿主的「乐享知识库」连接器：可能已过期或未连接，"
                    "重新连接后重试；或在 config.json 填 token 改为手填凭证。")
        return ("config.json 里的 token 无效或过期，重新获取：%s" % TOKEN_URL)

    # -- 业务调用 -----------------------------------------------------
    def call(self, name, arguments):
        """调一个工具，返回 `data`（接口只回文本时返回字符串）。业务错误抛 ApiError。"""
        inner = self._dispatch(name, arguments)
        if isinstance(inner, str):
            return inner
        code = inner.get("code")
        if code not in (0, None):
            msg = str(inner.get("message") or "")
            if code in (401, 403) or "unauthorized" in msg.lower() or "invalid token" in msg.lower():
                raise AuthError("乐享鉴权失败：%s（code=%s）\n  %s" % (msg, code, self._auth_hint()))
            raise ApiError("%s（code=%s）" % (msg or "接口报错", code))
        return _strip_entry_noise(inner.get("data") if "data" in inner else inner)

    # -- 目标解析 -----------------------------------------------------
    def resolve_target(self, url):
        """乐享目录 / 知识库链接 → {space_id, parent_entry_id, name, entry_type}。

        知识库链接取根节点当父目录（实测根节点可作导入父目录）。目录链接要求是 folder。
        结果按 URL 缓存，一次运行内不重复解析。
        """
        url = str(url or "").strip()
        if url in self._targets:
            return self._targets[url]
        entry_id, space_id = parse_lexiang_url(url)
        if space_id and not entry_id:
            space = self.call("space_describe_space", {"space_id": space_id}) or {}
            root = str((space.get("space") or space).get("root_entry_id") or "")
            if not root:
                raise ApiError("知识库 %s 的回包里没有 root_entry_id" % space_id)
            info = {"space_id": space_id, "parent_entry_id": root,
                    "name": str((space.get("space") or space).get("name") or ""),
                    "entry_type": "space"}
        elif entry_id:
            entry = (self.call("entry_describe_entry", {"entry_id": entry_id}) or {}).get("entry") or {}
            etype = str(entry.get("entry_type") or "")
            if etype and etype != "folder":
                raise ApiError("目标 %s 是 %s，不是目录。导入目标必须是目录或知识库链接。"
                               % (entry_id, etype))
            info = {"space_id": str(entry.get("space_id") or ""), "parent_entry_id": entry_id,
                    "name": str(entry.get("name") or ""), "entry_type": etype or "folder"}
            if not info["space_id"]:
                raise ApiError("目录 %s 的回包里没有 space_id" % entry_id)
        else:
            raise ApiError("无法从「%s」解析出乐享目录：请贴 …/pages/<32位id> 或 …/spaces/<32位id>"
                           % url)
        info["url"] = url
        self._targets[url] = info
        return info
