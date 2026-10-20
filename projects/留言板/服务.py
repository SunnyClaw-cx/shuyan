"""枢言 Web 应用 —— 网络适配层

职责边界（重要）：
    本文件**只做四件事**：监听端口、解析请求、调用枢言、回写响应。
    路由分发、状态码决策、HTML 渲染、数据增删 —— 全部在 `应用.sy` 里，
    由枢言编写。本文件不含任何业务逻辑。

为什么需要这一层：枢言标准库目前 11 个模块（文本/列表/映射/数学/随机/
时间/文件/系统/编码/加密/正则）都是**本地能力**，没有网络设施。
在 网络 模块落地之前，用宿主的薄适配层补上这一块。

运行：python projects/留言板/服务.py
     浏览器打开 http://127.0.0.1:8000
"""

import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from shuyan.builtins import StdLib
from shuyan.interpreter import Interpreter
from shuyan.lexer import Lexer
from shuyan.parser import Parser
from shuyan.semantic import Analyzer
from shuyan.source import Span
from shuyan.values import ShuyanMap

HOST = "127.0.0.1"
PORT = 8000
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "应用.sy")
SPAN = Span(0, 0, 0, 0)

# 解释器与入口函数，构建后填入
runtime: dict = {}


class Handler(BaseHTTPRequestHandler):
    """把一次 HTTP 请求翻译成一次枢言函数调用。"""

    server_version = "ShuyanWebApp/0.1"

    def do_GET(self):
        self._handle(self._path(), {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        self._handle(self._path(), form)

    def _path(self):
        """取出请求路径并**解码百分号编码**。

        中文路径在传输时是 `/%E6%96%B0%E5%A2%9E` 这种形式，
        若不解码，枢言侧拿到的是编码串，`路径 等于 "/新增"` 永远不成立。
        中文原生语言的服务端必须处理这一步 —— 否则「中文路由」形同虚设。
        """
        raw = self.path.split("?")[0]
        return urllib.parse.unquote(raw)

    def _handle(self, path, form):
        # 调用枢言：处理请求(路径, 表单) → {状态, 内容}
        resp = runtime["interp"]._call_shuyan(
            runtime["entry"], [path, ShuyanMap(form)], {}, SPAN)

        status = int(resp.get("状态", 200))
        body = str(resp.get("内容", "")).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", self._content_type(path))
        self.send_header("Content-Length", str(len(body)))
        if status == 302:
            self.send_header("Location", "/")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _content_type(path):
        if path == "/留言":
            return "application/json; charset=utf-8"
        return "text/html; charset=utf-8"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s %s\n" % (self.address_string(), fmt % args))


def build():
    """加载并运行枢言程序，取出入口函数。"""
    with open(APP, encoding="utf-8") as f:
        source = f.read()

    stdlib = StdLib(base_dir=os.path.dirname(APP))
    tokens = Lexer(source, APP).tokenize()
    program = Parser(tokens, APP).parse()

    analyzer = Analyzer(stdlib)
    analyzer.analyze(program)

    interp = Interpreter(stdlib, APP, source.splitlines(), analyzer)
    interp.run(program)

    runtime["interp"] = interp
    runtime["entry"] = interp.global_env.vars["处理请求"]


def main():
    build()
    server = ThreadingHTTPServer((HOST, PORT), Handler)

    print("枢言留言板已启动")
    print("  地址：http://%s:%d" % (HOST, PORT))
    print("  业务逻辑在 应用.sy（枢言编写）；本文件仅做网络收发")
    print("  按 Ctrl+C 停止")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()