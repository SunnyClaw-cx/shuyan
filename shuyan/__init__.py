"""枢言（Shuyan）—— 中文原生编程语言与自研编译器工具链。

本包为纯标准库实现，不依赖任何第三方库、语法文件或代码生成器。

模块地图：
    source.py       源码定位与东亚显示宽度
    diagnostics.py  诊断系统（中文报错、错误码、批量渲染）
    keywords.py     关键字表（三类切分行为）
    lexer.py        词法分析器（全角归一化 + 汉字三通道切分）
    ast_nodes.py    抽象语法树节点
    parser.py       语法分析器（双语端口归一）
    semantic.py     语义分析器（作用域、类型、静态检查）
    values.py       运行时值模型
    builtins.py     标准库
    interpreter.py  解释执行器
    formatter.py    格式化器
    linter.py       静态检查工具
    cli.py          命令行入口
"""

__version__ = "0.2.1"
__all__ = ["__version__"]