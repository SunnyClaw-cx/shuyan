"""sy.py —— 枢言命令行入口（供不安装直接运行）。

用法：
    python sy.py 运行 examples/你好世界.sy
    python sy.py 测试
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shuyan.cli import main

if __name__ == "__main__":
    sys.exit(main())