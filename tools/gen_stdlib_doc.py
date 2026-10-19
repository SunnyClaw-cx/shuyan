"""从源码自动生成标准库 API 文档（docs/STDLIB.md）。

**为什么自动生成**：手写的 API 文档必然与实现漂移。
本工具直接从 `builtins.StdLib` 的实际注册表与 `semantic` 的签名表导出，
保证文档与实现同源。

用法：python tools/gen_stdlib_doc.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shuyan.builtins import StdLib                       # noqa: E402
from shuyan.semantic import CORE_SIGNATURES, MODULE_SIGNATURES  # noqa: E402

# 人工撰写的说明（按名称索引）。文档的价值在于「怎么用」，
# 签名只能说明「怎么调」，故这部分必须人工维护。
DESCRIPTIONS: dict[str, str] = {
    # 核心 · 输入输出
    "输出": "打印值并换行。多个参数用空格分隔。别名：打印、说",
    "不换行输出": "打印值但不换行",
    "输入": "从标准输入读取一行，返回文本",
    # 核心 · 基本
    "长度": "返回文本的**字符数**（非字节数）、列表元素数或映射键数",
    "类型名": "返回值的中文类型名，如 \"整数\"、\"文本\"",
    "是否空": "判断值是否为假值（空、0、空文本、空列表、空映射）",
    "转文本": "转为文本。任何值都可转",
    "转整数": "转为整数。文本无法解析时抛异常。支持中文数词",
    "转小数": "转为小数",
    "转布尔": "按真值规则转为布尔",
    # 核心 · 数学
    "取整": "丢弃小数部分（向零取整）",
    "四舍五入": "四舍五入到指定小数位（默认为 0 位）",
    "取小": "返回两个数中较小的一个",
    "最大": "返回最大值。可传多个数，或传一个列表",
    "最小": "返回最小值。可传多个数，或传一个列表",
    "绝对值": "返回绝对值",
    "次方": "幂运算：次方(底, 指数)。等价于 底 ^ 指数",
    "求和": "对列表求和",
    "平均值": "求列表平均值，返回小数",
    "范围": "生成整数列表。范围(止)／范围(起, 止)／范围(起, 止, 步长)。**止不含**",
    # 核心 · 列表
    "排序": "返回排序后的**新列表**（不修改原列表）。第二参数传 `真` 表示倒序",
    "反转": "返回反转后的新列表",
    "连接": "把列表元素连成文本。连接(列表, 分隔符)",
    "分割": "按分隔符切分文本为列表。不传分隔符时按空白切分",
    "入列": "把元素加入列表末尾，返回该列表。别名：追加元素",
    "去重": "去重，保留首次出现的顺序",
    "切片": "切片(序列, 起, 止)，**止不含**。可用于列表与文本",
    "枚举": "返回 [[下标, 元素], ...] 形式的列表",
    # 核心 · 文本
    "截取": "截取(文本, 起, 长)，按**字符**计。起可为负（从末尾数）；省略长度则取到末尾",
    "查找": "返回子串首次出现的下标，找不到返回 -1",
    "替换": "替换所有出现的子串",
    "去空白": "去掉首尾空白（含全角空格）",
    "开头是": "判断文本是否以指定前缀开头",
    "结尾是": "判断文本是否以指定后缀结尾",
    "大写": "转为大写",
    "小写": "转为小写",
    "包含": "判断容器（列表/映射/文本）是否包含指定元素",
    "复制": "复制(文本, 次数, 分隔符?)，重复文本",
    # 核心 · 映射
    "取键": "返回映射的键列表",
    "取值": "返回映射的值列表",
    "有键": "判断映射是否含指定键",
    # 模块 · 文本
    "文本.填充": "填充(文本, 宽度, 填充符?, 对齐?)。按**显示宽度**补齐（汉字算 2 列）。对齐可选 左/右/中",
    "文本.重复": "同 `复制`，但经 `文本.重复` 访问以避免与关键字同名",
    "文本.逐字": "把文本拆成单字符列表，按 Unicode 码点",
    "文本.字符": "把码点转为字符",
    "文本.码点": "把字符转为 Unicode 码点",
    "文本.格式化": "格式化(模板, 映射)：把模板中的 {键} 替换为映射中的值",
    "文本.正则匹配": "返回所有匹配的子串列表",
    "文本.替换正则": "用正则替换",
    "文本.对齐": "同 `文本.填充`",
    "文本.显示宽度": "返回文本的显示宽度（汉字/全角算 2 列）",
    # 模块 · 列表
    "列表.去重": "同 `去重`",
    "列表.扁平": "把嵌套列表压平为一层",
    "列表.分组": "分组(列表, 每组大小)",
    "列表.压缩": "把两个列表配对为 [[a1,b1], [a2,b2], ...]",
    "列表.枚举": "同 `枚举`",
    "列表.切片": "同 `切片`",
    "列表.求和": "同 `求和`",
    "列表.排序": "同 `排序`",
    # 模块 · 映射
    "映射.合并": "合并多个映射为新的映射",
    "映射.键列表": "同 `取键`",
    "映射.值列表": "同 `取值`",
    "映射.有键": "同 `有键`",
    "映射.删除": "删除指定键并返回该映射",
    # 模块 · 数学
    "数学.圆周率": "圆周率 π",
    "数学.自然常数": "自然常数 e",
    "数学.正弦": "正弦（弧度）",
    "数学.余弦": "余弦（弧度）",
    "数学.正切": "正切（弧度）",
    "数学.平方根": "平方根。负数会抛异常",
    "数学.幂": "幂运算",
    "数学.对数": "对数，默认自然对数。第二参数可指定底",
    "数学.取整": "同 `取整`",
    "数学.向下取整": "向下取整（floor）",
    "数学.向上取整": "向上取整（ceil）",
    "数学.绝对值": "同 `绝对值`",
    "数学.最大": "同 `最大`",
    "数学.最小": "同 `最小`",
    # 模块 · 随机
    "随机.随机数": "随机数(止)／随机数(起, 止)，返回闭区间内的整数",
    "随机.随机小数": "返回 [0, 1) 之间的小数",
    "随机.选一个": "从列表中随机选一个元素",
    "随机.打乱": "返回打乱顺序的新列表",
    "随机.设随机种子": "设定随机种子，使结果可复现",
    # 模块 · 时间
    "时间.现在": "返回当前时间文本，格式 2026-10-19 15:35:41",
    "时间.时间戳": "返回当前 Unix 时间戳（秒，小数）",
    "时间.格式化": "格式化(时间戳, 格式?)，按 strftime 格式输出",
    "时间.休眠": "暂停指定秒数",
    "时间.年": "当前年份",
    "时间.月": "当前月份",
    "时间.日": "当前日",
    "时间.时": "当前小时",
    "时间.分": "当前分钟",
    "时间.秒": "当前秒",
    # 模块 · 文件
    "文件.读文本": "读取文件内容为文本（UTF-8）",
    "文件.写文本": "写入文本到文件（覆盖），目录不存在会自动创建",
    "文件.追加": "追加文本到文件末尾",
    "文件.存在": "判断路径是否存在",
    "文件.删除": "删除文件（只能删文件，不能删目录）",
    "文件.列表": "列出目录下的文件名",
    "文件.创建目录": "创建目录（含中间目录）",
    "文件.路径合并": "拼接路径片段",
    "文件.目录名": "取路径的目录部分",
    "文件.文件名": "取路径的文件名部分",
    # 模块 · 系统
    "系统.参数": "返回命令行参数列表",
    "系统.环境变量": "读取环境变量，可给默认值",
    "系统.平台": "返回平台名：Windows / macOS / Linux",
    "系统.退出": "退出程序，可指定退出码",
    "系统.执行": "执行外部命令，返回 {退出码, 输出, 错误}",
    "系统.当前目录": "返回当前工作目录",
    # 模块 · 编码
    "编码.编码Base64": "Base64 编码（UTF-8）",
    "编码.解码Base64": "Base64 解码为文本",
    "编码.编码十六进制": "转为十六进制文本",
    "编码.解码十六进制": "十六进制文本解码",
    # 模块 · 加密
    "加密.哈希": "哈希(文本, 算法?)，算法可选 md5/sha1/sha256/sha512，默认 sha256",
    "加密.MD5": "MD5 摘要（十六进制小写）",
    "加密.SHA256": "SHA-256 摘要（十六进制小写）",
    # 模块 · 正则
    "正则.匹配": "返回所有匹配的子串",
    "正则.替换": "用正则替换",
    "正则.是否匹配": "判断是否有匹配",
    "正则.分组": "返回所有匹配的捕获组",
}

CATEGORY_TITLES = {
    "输入输出": "输入输出",
    "基本": "类型与转换",
    "数学": "数学",
    "列表": "列表与序列",
    "文本": "文本处理",
    "映射": "映射",
}


def _sig_text(name: str, sig: dict) -> str:
    if name not in sig:
        return "—"
    need, most, ret = sig[name]
    if most is None:
        argc = f"至少 {need}"
    elif need == most:
        argc = str(need)
    else:
        argc = f"{need}–{most}"
    return f"{argc} 个参数 · 返回 {ret}"


def main() -> int:
    stdlib = StdLib()
    lines: list[str] = []
    lines.append("# 枢言标准库 API 参考")
    lines.append("")
    lines.append("> 本文件由 `tools/gen_stdlib_doc.py` **自动生成**，"
                 "内容与实现同源。请勿手工编辑。")
    lines.append(">")
    lines.append("> 生成命令：`python tools/gen_stdlib_doc.py`")
    lines.append("")
    lines.append("## 目录")
    lines.append("")
    lines.append("- [一、核心函数](#一核心函数)（无需引入，直接调用）")
    lines.append("- [二、标准模块](#二标准模块)（需 `引入 \"模块名\"` 后使用）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、核心函数")
    lines.append("")
    lines.append("无需 `引入`，直接调用。")
    lines.append("")

    core_names = stdlib.core_names()
    # 按签名表顺序分组呈现（更贴近用户心智）
    grouped: dict[str, list[str]] = {}
    order = list(CORE_SIGNATURES.keys())
    for name in order:
        if name not in core_names:
            continue
        grouped.setdefault(_core_category(name), []).append(name)
    for extra in core_names:
        if all(extra not in v for v in grouped.values()):
            grouped.setdefault("其他", []).append(extra)

    for cat, names in grouped.items():
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("| 函数 | 参数 | 返回 | 说明 |")
        lines.append("|---|---|---|---|")
        for name in names:
            sig = CORE_SIGNATURES.get(name)
            argc = _sig_text(name, CORE_SIGNATURES)
            ret = sig[2] if sig else "任意"
            desc = DESCRIPTIONS.get(name, "")
            lines.append(f"| `{name}` | {argc.split(' · ')[0]} | {ret} | {desc} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 二、标准模块")
    lines.append("")
    lines.append("用法：先 `引入 \"模块名\"`，再经 `模块名.成员名` 调用。")
    lines.append("")

    for mod in stdlib.module_names():
        members = sorted(stdlib.modules[mod])
        lines.append(f"### {mod}")
        lines.append("")
        lines.append("| 函数 | 参数 | 返回 | 说明 |")
        lines.append("|---|---|---|---|")
        for member in members:
            key = f"{mod}.{member}"
            sig = MODULE_SIGNATURES.get(key)
            argc = _sig_text(key, MODULE_SIGNATURES)
            ret = sig[2] if sig else "任意"
            desc = DESCRIPTIONS.get(key, "")
            lines.append(f"| `{mod}.{member}` | {argc.split(' · ')[0]} | "
                         f"{ret} | {desc} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三、中文处理要点")
    lines.append("")
    lines.append("枢言对中文的处理有两处与直觉不同，务必记住：")
    lines.append("")
    lines.append("1. **长度按字符算，不按字节** —— `长度(\"你好\")` 返回 `2`，不是 6。")
    lines.append("2. **对齐按显示宽度算** —— 汉字占 2 列。"
                 "`文本.填充(\"你好\", 10)` 会补 6 个空格，"
                 "使终端表格对齐。`文本.显示宽度(\"你好\")` 返回 `4`。")
    lines.append("")
    lines.append("此外，以下函数对中文有特别支持：")
    lines.append("")
    lines.append("| 函数 | 中文相关行为 |")
    lines.append("|---|---|")
    lines.append("| `转整数` | 支持中文数词：`转整数(\"一百二十\")` → `120` |")
    lines.append("| `文本.逐字` | 按 Unicode 码点拆分，中文一个字一个元素 |")
    lines.append("| `文本.填充` | 按显示宽度对齐，汉字算 2 列 |")
    lines.append("| `比较`（运算符） | 文本按码点逐字比较，中文可排序 |")
    lines.append("")
    lines.append("**限制说明**：字符处理按 **Unicode 码点**，未做字形簇（grapheme cluster）"
                 "合并。因此 emoji 组合符（如带肤色的人脸）会被视为多个字符。"
                 "这是 v0.1 的已知限制。")
    lines.append("")

    out_path = os.path.join(ROOT, "docs", "STDLIB.md")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"已生成 {out_path}")
    print(f"  核心函数 {len(core_names)} 个，"
          f"模块 {len(stdlib.module_names())} 个，"
          f"模块成员 {sum(len(v) for v in stdlib.modules.values())} 个")
    return 0


def _core_category(name: str) -> str:
    table = {
        "输入输出": ["输出", "打印", "说", "不换行输出", "输入"],
        "基本": ["长度", "类型名", "是否空", "转文本", "转整数", "转小数",
                 "转布尔", "断言失败信息"],
        "数学": ["取整", "四舍五入", "取小", "最大", "最小", "绝对值", "次方",
                 "幂运算", "求和", "平均值", "范围"],
        "列表": ["排序", "反转", "连接", "分割", "入列", "追加元素", "去重",
                 "切片", "枚举"],
        "文本": ["截取", "查找", "替换", "去空白", "开头是", "结尾是", "大写",
                 "小写", "包含", "复制"],
        "映射": ["取键", "取值", "有键"],
    }
    for cat, names in table.items():
        if name in names:
            return cat
    return "其他"


if __name__ == "__main__":
    sys.exit(main())