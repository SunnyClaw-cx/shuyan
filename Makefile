# 枢言（Shuyan）· 常用任务
# 用法：make 帮助
#
# 本 Makefile 仅为便利，所有命令均可直接用 python 调用。
# Windows 用户若无 make，请直接执行注释中的等价命令。

PY ?= python

.DEFAULT_GOAL := 帮助

.PHONY: 帮助 测试 单元测试 示例 工程 应用自测 检查 词法 语法树 格式化 文档 基准 全量 清理 安装

帮助:
	@echo "枢言（Shuyan）常用任务"
	@echo ""
	@echo "  make 测试        运行内置自测（52 项，必须全绿）"
	@echo "  make 单元测试    运行 tests/ 下的标准化单元测试"
	@echo "  make 示例        运行 examples/ 下全部示例程序"
	@echo "  make 工程        运行 projects/ 下样例工程的主程序"
	@echo "  make 应用自测    端到端自测 Web 应用（21 项，真实 HTTP 请求）"
	@echo "  make 检查        静态检查 examples/ 与 projects/（必须零问题）"
	@echo "  make 文档        从源码重新生成 docs/STDLIB.md"
	@echo "  make 基准        运行性能基准套件"
	@echo "  make 全量        完整验证（提交前必跑）"
	@echo "  make 格式化      格式化 examples/ 下全部源码（就地写入）"
	@echo "  make 清理        清理 Python 缓存与构建产物"
	@echo "  make 安装        以可编辑模式安装为 sy 命令"
	@echo ""
	@echo "环境变量 PY 可指定解释器，例如：make 测试 PY=python3.13"

测试:
	$(PY) sy.py 测试

单元测试:
	$(PY) -m unittest discover -s tests -v

示例:
	@for f in examples/*.sy; do \
		echo "── $$f ──"; \
		$(PY) sy.py 运行 "$$f" || exit 1; \
	done

工程:
	$(PY) sy.py 运行 projects/库存管理/主程序.sy

应用自测:
	$(PY) projects/留言板/自测.py

检查:
	$(PY) sy.py 检查 examples/*.sy projects/*/*.sy

词法:
	$(PY) sy.py 词法 examples/中文切分演示.sy

语法树:
	$(PY) sy.py 语法树 examples/你好世界.sy

格式化:
	$(PY) sy.py 格式化 examples/*.sy --写入

文档:
	$(PY) tools/gen_stdlib_doc.py

基准:
	$(PY) benchmarks/run_bench.py

# 提交前必跑：全部通过才算完成
全量: 测试 单元测试 示例 工程 应用自测 检查 文档 基准
	@echo ""
	@echo "═══ 全量验证完成 ═══"

清理:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.py[cod]" -delete 2>/dev/null || true
	@rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache 2>/dev/null || true
	@echo "已清理缓存与构建产物。"

安装:
	$(PY) -m pip install -e .
	@echo "已安装。现在可以直接运行：sy 运行 examples/你好世界.sy"