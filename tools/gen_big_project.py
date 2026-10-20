"""枢言 —— 大型工程生成器（可复现的 10 万行级企业工程）

用途：按**确定性规则**生成一套多文件枢言工程，用于压力测试与极端场景验证。
所有名字、金额、数量都由固定种子推导，**同一命令两次运行结果逐字节一致**
（可复现是所有对外数字的前提）。

用法：
    python tools/gen_big_project.py <输出目录> [--modules N] [--seed S]

设计要点：
  · 每个模块文件有独立的领域前缀，模块之间**不循环引入**（会触发 D3020）
  · 类型定义集中在 `基础类型.sy`，各业务模块**按名引入**所需类型
    （SPEC §3.8.1：类型注解处必须能裸用类型名，故用按名引入）
  · 金额一律整数分；数量一律整数
  · 生成的内容**全是合法程序**，不含故意错误 —— 错误场景由另一支探针覆盖
"""

from __future__ import annotations

import argparse
import os
import random
import sys

# ---------------------------------------------------------------- 固定语料

# 中文物料/单位名（组合生成，避免重复）
品类 = ["大蒜粉", "辣椒粉", "姜粉", "洋葱粉", "白胡椒", "黑胡椒", "八角粉",
        "肉桂粉", "孜然粉", "花椒粉", "五香粉", "十三香", "咖喱粉", "芥末粉",
        "番茄粉", "菠菜粉", "胡萝卜粉", "南瓜粉", "紫薯粉", "玉米淀粉"]
规格 = ["一级", "特级", "优级", "出口级", "工业级", "食品级"]
包装 = ["箱", "袋", "桶", "包", "件"]

单位词 = ["商贸", "贸易", "食品", "实业", "供应链", "进出口", "物流", "粮油",
          "农产", "调味"]
城市 = ["金乡", "临沂", "青岛", "烟台", "潍坊", "济宁", "菏泽", "日照", "枣庄",
        "德州", "聊城", "滨州"]

科目词 = ["库存商品", "应收账款", "应付账款", "预付账款", "其他应收款",
          "原材料", "包装物", "低值易耗品", "累计折旧", "固定资产",
          "主营业务收入", "其他业务收入", "主营业务成本", "销售费用",
          "管理费用", "财务费用", "税金及附加", "待处理财产损溢",
          "实收资本", "盈余公积", "未分配利润", "短期借款"]


def _ident(prefix: str, i: int) -> str:
    """生成稳定的中文标识符（同一 i 永远得到同一个名字）。"""
    return f"{prefix}{i:05d}"


# ---------------------------------------------------------------- 模块生成

HEAD = """// ============================================================
// {title}
//
// 本文件由工程生成器按确定性规则产出，用于**大型工程压力测试**。
// 生成规则见 `tools/gen_big_project.py`；同一命令两次运行结果逐字节一致。
// ============================================================

{imports}
"""


def gen_basic(rng: random.Random, n_types: int) -> str:
    """基础类型模块：枚举 + 结构体（全工程类型的唯一定义处）。"""
    out = ["""// 基础类型：全工程共用的枚举与结构体。
//
// 这是依赖关系的最底层，**不引入任何业务模块**。
// 所有业务模块从这里按名引入所需类型 —— 类型只有一份定义，
// 杜绝「同一结构两处各写一遍」。

// 金额一律**整数分**，数量一律**整数**。

引入 "文本"
引入 "数学"

类型 单据状态 = { 草稿, 已审核, 已执行, 已完成, 已作废 }
类型 借贷方向 = { 借, 贷 }
类型 科目类别 = { 资产, 负债, 所有者权益, 成本, 损益 }
类型 物料类别 = { 原材料, 半成品, 成品, 包装物, 备件 }
类型 单位类别 = { 供应商, 客户, 二者皆是 }

"""]

    # 结构体：每个都是「编号 + 名称 + 金额 + 数量 + 状态」的合理变体
    for i in range(1, n_types + 1):
        name = _ident("结构", i)
        out.append(f"类型 {name} = {{")
        out.append(f"    编号: 文本,")
        out.append(f"    名称: 文本,")
        out.append(f"    金额: 整数,")
        out.append(f"    数量: 整数,")
        out.append(f"    状态: 单据状态")
        out.append("}")
        out.append("")

    # 对应的构造辅助（字段顺序只在此处维护一次）
    for i in range(1, n_types + 1):
        name = _ident("结构", i)
        out.append(f"请 造{name}(编号, 名称, 金额, 数量, 状态)")
        out.append(f"    返回 {name}(编号 是 编号, 名称 是 名称, 金额 是 金额,")
        out.append(f"    数量 是 数量, 状态 是 状态)")
        out.append("结束")
        out.append("")

    # 查询与汇总函数
    out.append("""请 取编号(某记录)
    返回 某记录.编号
结束

请 取名称(某记录)
    返回 某记录.名称
结束

请 取金额(某记录)
    返回 某记录.金额
结束

请 取数量(某记录)
    返回 某记录.数量
结束

请 已生效(某记录)
    返回 某记录.状态 是 单据状态.已完成 或 某记录.状态 是 单据状态.已执行
结束

请 状态文本(某记录)
    如果 某记录.状态 是 单据状态.草稿 则
        返回 "草稿"
    结束
    如果 某记录.状态 是 单据状态.已审核 则
        返回 "已审核"
    结束
    如果 某记录.状态 是 单据状态.已执行 则
        返回 "已执行"
    结束
    如果 某记录.状态 是 单据状态.已完成 则
        返回 "已完成"
    结束
    返回 "已作废"
结束

请 金额合计(全部记录)
    定义 合计 = 0
    遍历 全部记录 中的每个 某记录
        合计 = 合计 + 某记录.金额
    结束
    返回 合计
结束

请 数量合计(全部记录)
    定义 合计 = 0
    遍历 全部记录 中的每个 某记录
        合计 = 合计 + 某记录.数量
    结束
    返回 合计
结束

请 按状态筛选(全部记录, 想要状态)
    定义 结果 = []
    遍历 全部记录 中的每个 某记录
        如果 某记录.状态 是 想要状态 则
            入列(结果, 某记录)
        结束
    结束
    返回 结果
结束

// `排序依据` 的取键函数是**单参**（`函数(x) 返回 键 结束`）——
// 不是双参比较器。这一点极易写错：若写成 `函数(甲, 乙)`，运行期会报
// 「至少需要 2 个参数，实际给了 1 个」，且函数名为空（匿名函数），
// 提示信息里看不出是哪个回调出的错。
请 按金额排序(全部记录)
    返回 排序依据(全部记录, 函数(某记录)
        返回 某记录.金额
    结束)
结束

请 按金额降序(全部记录)
    返回 排序依据(全部记录, 函数(某记录)
        返回 某记录.金额
    结束, 真)
结束

请 最大金额记录(全部记录)
    // 变量名不用 `最大`：那会遮蔽内置函数 `最大`（触发 W4001）。
    // 生成器产出的工程必须**零警告**，否则「检查全绿」就不再是硬指标。
    定义 最大金额 = 0
    定义 找到 = 空
    遍历 全部记录 中的每个 某记录
        如果 某记录.金额 > 最大金额 则
            最大金额 = 某记录.金额
            找到 = 某记录
        结束
    结束
    返回 找到
结束

// ------------------------------------------------------------ 编号工具
//
// 编号格式只有这一份实现：改宽度只改这里，全工程一致。

请 补零(数, 宽度)
    定义 原文 = 转文本(数)
    定义 差 = 宽度 - 长度(原文)
    如果 差 <= 0 则
        返回 原文
    结束
    返回 文本.重复("0", 差) + 原文
结束

请 编单号(前缀, 序号)
    返回 前缀 + "-" + 补零(序号, 6)
结束
""")
    return "\n".join(out)


def gen_service(rng: random.Random, idx: int, n_funcs: int,
                type_name: str, basic_mod: str) -> str:
    """生成一个业务服务模块：纯函数，计算为主。"""
    prefix = f"服务{idx:03d}"
    imports = (f'引入 "{basic_mod}"\n'
               f'引入 {{ 单据状态 }} 从 "{basic_mod}"\n'
               f'引入 "数学"')
    body = [HEAD.format(title=f"{prefix} —— 业务计算（纯函数）", imports=imports)]

    body.append(f"""
// 本模块只做计算：**读入参、不改入参、只返回值**。
// 状态一律由上层（主程序）持有；服务层若随手改入参，
// 调用方持有的对象会被悄悄改掉（枢言的赋值是**引用语义**）。

请 {prefix}_校验(记录)
    如果 记录.数量 < 0 则
        返回 [假, "数量不能为负"]
    结束
    如果 记录.金额 < 0 则
        返回 [假, "金额不能为负"]
    结束
    返回 [真, "通过"]
结束

请 {prefix}_单位金额(记录)
    如果 记录.数量 是 0 则
        返回 0
    结束
    返回 数学.向下取整(记录.金额 / 记录.数量)
结束
""")

    for k in range(1, n_funcs + 1):
        body.append(f"""
请 {prefix}_计算{k}(记录, 系数)
    定义 基数 = 记录.金额 * 系数
    定义 调整 = 基数 + 记录.数量 * {k}
    如果 调整 < 0 则
        返回 0
    结束
    返回 调整
结束

请 {prefix}_汇总{k}(全部记录)
    定义 合计 = 0
    遍历 全部记录 中的每个 某记录
        合计 = 合计 + {prefix}_计算{k}(某记录, 1)
    结束
    返回 合计
结束
""")

    # 一条较长的调用链，验证跨函数调用深度。
    # 步数取 `min(n_funcs, 8)` —— 若硬编码 4 步而 n_funcs < 4，
    # 会生成对不存在函数的调用（这是生成器的 bug，不是语言的问题，
    # 但会让「工程必须全绿」这个硬指标失效）。
    steps = min(n_funcs, 8)
    if steps >= 2:
        body.append(f"\n请 {prefix}_链式(记录)\n")
        body.append("    定义 累计 = 记录.金额\n")
        for k in range(1, steps + 1):
            body.append(f"    累计 = {prefix}_计算{k}(记录, 累计)\n")
        body.append("    返回 累计\n结束\n")
    else:
        body.append(f"\n请 {prefix}_链式(记录)\n"
                    f"    返回 {prefix}_计算1(记录, 1)\n结束\n")
    return "".join(body)


def gen_repo(rng: random.Random, idx: int, n_funcs: int, basic_mod: str) -> str:
    """生成一个「仓储/查询」模块：集合操作、映射、字符串处理。"""
    prefix = f"仓储{idx:03d}"
    imports = (f'引入 "{basic_mod}"\n'
               f'引入 {{ 单据状态 }} 从 "{basic_mod}"\n'
               f'引入 "文本"\n引入 "数学"')
    body = [HEAD.format(title=f"{prefix} —— 查询与集合操作", imports=imports)]

    body.append(f"""
请 {prefix}_按编号找(全部记录, 编号)
    遍历 全部记录 中的每个 某记录
        如果 某记录.编号 是 编号 则
            返回 某记录
        结束
    结束
    返回 空
结束

请 {prefix}_按状态计数(全部记录)
    定义 计数 = {{}}
    遍历 全部记录 中的每个 某记录
        定义 键 = {basic_mod}.状态文本(某记录)
        如果 非 有键(计数, 键) 则
            计数[键] = 0
        结束
        计数[键] = 计数[键] + 1
    结束
    返回 计数
结束

请 {prefix}_分页(全部记录, 页码, 每页)
    定义 起始 = (页码 - 1) * 每页
    如果 起始 >= 长度(全部记录) 则
        返回 []
    结束
    定义 结束位 = 起始 + 每页
    如果 结束位 > 长度(全部记录) 则
        结束位 = 长度(全部记录)
    结束
    返回 切片(全部记录, 起始, 结束位)
结束

请 {prefix}_编号表(全部记录)
    定义 结果 = []
    遍历 全部记录 中的每个 某记录
        入列(结果, 某记录.编号)
    结束
    返回 结果
结束

请 {prefix}_对齐输出(记录, 宽度)
    返回 文本.填充(记录.编号, 宽度, " ", "左") + " " +
        文本.填充(记录.名称, 宽度, " ", "左")
结束
""")

    for k in range(1, n_funcs + 1):
        body.append(f"""
请 {prefix}_过滤{k}(全部记录, 下限)
    定义 结果 = []
    遍历 全部记录 中的每个 某记录
        如果 某记录.金额 >= 下限 且 某记录.数量 >= {k} 则
            入列(结果, 某记录)
        结束
    结束
    返回 结果
结束

请 {prefix}_映射{k}(全部记录)
    定义 结果 = []
    遍历 全部记录 中的每个 某记录
        入列(结果, [{k}, 某记录.编号, 某记录.金额])
    结束
    返回 结果
结束
""")
    return "".join(body)


def gen_ledger(rng: random.Random, idx: int, n_funcs: int, basic_mod: str) -> str:
    """生成一个「记账/财务」模块：复式记账的铁律校验。"""
    prefix = f"账簿{idx:03d}"
    imports = (f'引入 "{basic_mod}"\n'
               f'引入 {{ 借贷方向 }} 从 "{basic_mod}"\n'
               f'引入 "数学"')
    body = [HEAD.format(title=f"{prefix} —— 借贷与余额", imports=imports)]

    body.append(f"""
请 {prefix}_借方合计(分录表)
    定义 合计 = 0
    遍历 分录表 中的每个 某分录
        如果 某分录.方向 是 借贷方向.借 则
            合计 = 合计 + 某分录.金额
        结束
    结束
    返回 合计
结束

请 {prefix}_贷方合计(分录表)
    定义 合计 = 0
    遍历 分录表 中的每个 某分录
        如果 某分录.方向 是 借贷方向.贷 则
            合计 = 合计 + 某分录.金额
        结束
    结束
    返回 合计
结束

请 {prefix}_平不平(分录表)
    返回 {prefix}_借方合计(分录表) 是 {prefix}_贷方合计(分录表)
结束

请 {prefix}_差额(分录表)
    返回 {prefix}_借方合计(分录表) - {prefix}_贷方合计(分录表)
结束
""")

    for k in range(1, n_funcs + 1):
        body.append(f"""
请 {prefix}_净额{k}(分录表)
    定义 借 = 0
    定义 贷 = 0
    遍历 分录表 中的每个 某分录
        如果 某分录.方向 是 借贷方向.借 则
            借 = 借 + 某分录.金额 * {k}
        否则
            贷 = 贷 + 某分录.金额 * {k}
        结束
    结束
    返回 借 - 贷
结束
""")
    return "".join(body)


def gen_main(rng: random.Random, services, repos, ledgers, basic_mod,
             n_records: int) -> str:
    """生成主程序：构造数据 → 调用各服务 → 输出结果（必须真跑通）。"""
    imports = [f'引入 "{basic_mod}"',
               f'引入 {{ 单据状态, 借贷方向 }} 从 "{basic_mod}"',
               f'引入 "文本"', f'引入 "数学"', f'引入 "列表"']
    for s in services:
        imports.append(f'引入 "{s}"')
    for r in repos:
        imports.append(f'引入 "{r}"')
    for l in ledgers:
        imports.append(f'引入 "{l}"')

    out = [HEAD.format(title="主程序 —— 大型工程端到端演示", imports="\n".join(imports))]
    # **辅助函数必须定义在调用之前**：枢言是解释执行，函数体在被调用时
    # 才求值，故 `定义`（函数声明）必须先执行到。放在文件末尾会报
    # 「没有定义 主数据换算」—— 这是解释型语言的固有顺序要求，
    # 不是缺陷，但生成器必须遵守。
    out.append("""
请 元金额(分)
    定义 元 = 数学.向下取整(分 / 100)
    定义 角 = 分 % 100
    返回 转文本(元) + "." + 文本.填充(转文本(角), 2, "0", "左")
结束
""")
    out.append("输出(\"════════════════════════════════════════════════\")")
    out.append("输出(\" 大型工程压测 · 数据装载\")")
    out.append("输出(\"════════════════════════════════════════════════\")")
    out.append("")

    # 构造记录（用第一个结构体）
    st = _ident("结构", 1)
    out.append(f"定义 全部记录 = []")
    states = ["草稿", "已审核", "已执行", "已完成", "已作废"]
    for i in range(n_records):
        st_name = states[i % len(states)]
        amt = (i + 1) * 137
        qty = (i % 37) + 1
        # 拆成多行：行尾放 `,` 可自动续行（括号内也忽略换行），
        # 使每行不超过规范 100 显示列 —— 否则会刷出大量 W4202。
        out.append(f'入列(全部记录,')
        out.append(f'    基础类型.造{st}(')
        out.append(f'        基础类型.编单号("R", {i + 1}),')
        out.append(f'        "记录" + 转文本({i + 1}),')
        out.append(f'        {amt}, {qty},')
        out.append(f'        单据状态.{st_name}))')
    out.append("")
    out.append('输出("装载记录数：" + 转文本(长度(全部记录)))')
    out.append('输出("金额合计：" + 元金额(基础类型.金额合计(全部记录)))')
    out.append("")

    # 调用每个服务的一条链式函数
    for s in services:
        out.append(f'输出("{s} 链式结果：" + 转文本({s}.{s}_链式(全部记录[0])))')
    for r in repos:
        out.append(f'输出("{r} 编号表条目：" + 转文本(长度({r}.{r}_编号表(全部记录))))')
        out.append(f'输出("{r} 分页第 1 页：" + 转文本(长度({r}.{r}_分页(全部记录, 1, 10))))')
    out.append("")

    # 按状态筛选 + 排序 + 求最大
    out.append('定义 已完成的 = 基础类型.按状态筛选(全部记录, 单据状态.已完成)')
    out.append('输出("已完成条数：" + 转文本(长度(已完成的)))')
    out.append('定义 排序后 = 基础类型.按金额排序(全部记录)')
    out.append('输出("排序后条数：" + 转文本(长度(排序后)))')
    out.append('输出("金额最大：" + 转文本(基础类型.最大金额记录(全部记录).金额))')
    out.append("")

    # 复式记账：构造一张平衡的分录表并校验
    if ledgers:
        l = ledgers[0]
        out.append("定义 分录表 = [")
        out.append('{"方向": 借贷方向.借, "金额": 123456},')
        out.append('{"方向": 借贷方向.贷, "金额": 123456}]')
        out.append(f'输出("借贷平衡：" + 转文本({l}.{l}_平不平(分录表)))')
        out.append(f'输出("借方合计：" + 转文本({l}.{l}_借方合计(分录表)))')
        out.append("")

    out.append('输出("════════════════════════════════════════════════")')
    out.append('输出(" 演示结束")')
    out.append('输出("════════════════════════════════════════════════")')

    return "\n".join(out)


# ---------------------------------------------------------------- 主流程

def build(outdir: str, n_modules: int, seed: int, n_records: int,
          n_funcs: int, n_types: int) -> dict:
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(seed)

    basic_mod = "基础类型"
    files: dict[str, str] = {}
    files[basic_mod + ".sy"] = gen_basic(rng, n_types)

    # 业务模块：服务 / 仓储 / 账簿 三类轮转
    services, repos, ledgers = [], [], []
    for i in range(1, n_modules + 1):
        kind = i % 3
        if kind == 1:
            name = f"服务{i:03d}"
            files[name + ".sy"] = gen_service(rng, i, n_funcs, None, basic_mod)
            services.append(name)
        elif kind == 2:
            name = f"仓储{i:03d}"
            files[name + ".sy"] = gen_repo(rng, i, n_funcs, basic_mod)
            repos.append(name)
        else:
            name = f"账簿{i:03d}"
            files[name + ".sy"] = gen_ledger(rng, i, n_funcs, basic_mod)
            ledgers.append(name)

    files["主程序.sy"] = gen_main(rng, services, repos, ledgers, basic_mod,
                                  n_records)

    total_lines = 0
    for fname, text in files.items():
        path = os.path.join(outdir, fname)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        total_lines += text.count("\n") + 1

    return {"files": len(files), "lines": total_lines,
            "services": len(services), "repos": len(repos),
            "ledgers": len(ledgers)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--modules", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--records", type=int, default=200)
    ap.add_argument("--funcs", type=int, default=24)
    ap.add_argument("--types", type=int, default=30)
    args = ap.parse_args()

    info = build(args.outdir, args.modules, args.seed, args.records,
                 args.funcs, args.types)
    print(f"生成完成：{info['files']} 个文件，{info['lines']} 行")
    print(f"  服务 {info['services']} · 仓储 {info['repos']} · 账簿 {info['ledgers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())