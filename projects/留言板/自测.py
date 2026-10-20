"""枢言留言板 —— 端到端自测

启动真实 HTTP 服务，用真实请求跑完全部用例，逐项打印结果。

运行：python projects/留言板/自测.py

设计原则（与项目其余部分一致）：
    断言的是**用户实际能看到的东西** —— HTTP 状态码、响应头、响应体内容，
    而不是内部函数返回值。内部函数跑通不等于应用跑通。
"""

import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import 服务  # noqa: E402

HOST = "127.0.0.1"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向。

    `urllib` 默认会跟随 302，于是「提交后重定向」在测试里表现成 200，
    把重定向验证变成**假通过**。要测到真实状态码就得自己接管。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)

_RESULTS = []


def _check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    mark = "✓" if cond else "✗"
    line = "  %s %s" % (mark, name)
    if detail and not cond:
        line += "   （%s）" % detail
    print(line)


def _get(base, path):
    """发 GET 请求，返回（状态码, 响应体文本, 响应头）。"""
    url = base + urllib.parse.quote(path)
    try:
        resp = _opener.open(url, timeout=5)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), exc.headers
    return resp.status, resp.read().decode("utf-8"), resp.headers


def _post(base, path, data):
    """发 POST 请求（表单），返回（状态码, 响应体文本, 响应头）。"""
    url = base + urllib.parse.quote(path)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        resp = _opener.open(req, timeout=5)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), exc.headers
    return resp.status, resp.read().decode("utf-8"), resp.headers


def main():
    服务.build()
    server = ThreadingHTTPServer((HOST, 0), 服务.Handler)
    port = server.server_address[1]
    base = "http://%s:%d" % (HOST, port)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("枢言留言板 · 端到端自测（端口 %d）\n" % port)
    try:
        # ---- 空状态 ----
        code, body, _ = _get(base, "/")
        _check("GET / 返回 200", code == 200, "实际 %s" % code)
        _check("首页返回完整 HTML", body.strip().startswith("<!DOCTYPE html>"))
        _check("空状态提示「还没有留言」", "还没有留言" in body)
        _check("初始计数为 0", "共 0 条留言" in body)

        # ---- 新增留言（PRG）----
        code, _, headers = _post(base, "/新增",
                                 {"name": "张三", "text": "第一条留言"})
        _check("POST /新增 返回 302（PRG 模式）", code == 302, "实际 %s" % code)
        _check("302 带 Location: /", headers.get("Location") == "/",
               "实际 %s" % headers.get("Location"))

        # ---- 提交后状态更新 ----
        code, body, _ = _get(base, "/")
        _check("提交后计数变为 1", "共 1 条留言" in body)
        _check("提交后列表含新留言内容", "第一条留言" in body)
        _check("提交后列表含提交人姓名", "张三" in body)

        # ---- 空白内容被拒绝 ----
        _post(base, "/新增", {"name": "李四", "text": "   "})
        code, body, _ = _get(base, "/")
        _check("空白内容被拒绝入库（计数仍为 1）", "共 1 条留言" in body,
               "实际页面未保持计数 1")

        # ---- 缺字段保护 ----
        _post(base, "/新增", {"text": "没有姓名"})
        code, body, _ = _get(base, "/")
        _check("缺姓名时用「匿名」兜底", "匿名" in body)

        # ---- JSON 接口 ----
        code, body, headers = _get(base, "/留言")
        _check("GET /留言 返回 200", code == 200, "实际 %s" % code)
        _check("JSON 接口 Content-Type 正确",
               "application/json" in (headers.get("Content-Type") or ""),
               "实际 %s" % headers.get("Content-Type"))
        _check("JSON 接口返回数组", body.strip().startswith("[")
               and body.strip().endswith("]"), "实际 %r" % body[:40])
        _check("JSON 含已提交内容", "第一条留言" in body)

        # ---- 中文路由与编码 ----
        # `GET /新增` 会走进 `/新增` 分支并返回 302。**302 恰恰是路径被正确
        # 解码并匹配的证据** —— 若解码失败，会落进最后一行返回 404。
        # 注意：本示例路由**只按路径分发，不区分 HTTP 方法**（见 README 边界声明），
        # 故对表单端点发 GET 也会得到 302 而非 405。断言按此口径写。
        code, _, headers = _get(base, "/新增")
        _check("中文路由 /新增 被正确解码并匹配（非 404）", code == 302,
               "实际 %s" % code)
        _check("中文路由匹配到的是 /新增 分支", code in (200, 302),
               "实际 %s" % code)

        code, _, headers = _get(base, "/留言")
        _check("中文页面 Content-Type 为 UTF-8",
               "charset=utf-8" in (headers.get("Content-Type") or ""),
               "实际 %s" % headers.get("Content-Type"))

        _, body, _ = _get(base, "/")
        _check("中文页面编码正确（标题为「留言板」）", "留言板" in body)

        # ---- 404 兜底 ----
        code, body, _ = _get(base, "/不存在的路径")
        _check("未知路径返回 404", code == 404, "实际 %s" % code)
        _check("404 响应含提示路径", "找不到" in body)
    finally:
        server.shutdown()
        server.server_close()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n共 %d 项，通过 %d，失败 %d" % (total, passed, total - passed))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())