"""概念标签 → 交易叙事归并 + 噪声过滤（issue #317 W2 信息流增强）。

问题
----
东财概念板块一次性返回 500+ 个标签，其中大量标签**不构成可交易的叙事**：

- **噪声类**：融资融券、深股通、昨日涨停、机构重仓、沪深300_ …… 这些是
  指数成分 / 交易机制 / 持仓属性标签，跟着大盘动，对「今天的主线是什么」
  零信息量，却会挤占信息流前 20 名。
- **结构类**：长江三角、深圳特区 …… 地域标签，属于「公司注册地」而非
  「资金正在交易的方向」。
- **同义碎片**：``CPO`` / ``光通信模块`` / ``光模块`` 是同一个叙事；
  ``AI智能体`` / ``AIGC`` / ``DeepSeek`` 同属 AI 应用。逐条展示等于把
  一个主线拆成 5 条，稀释信号。

本模块提供一层**纯映射**：标签 → 叙事名 + 角色判定。不含阈值、不碰网络、
不依赖时间，因此可完全单测，也可安全用于回测（无未来数据风险）。

移植说明（许可证边界）
----------------------
「概念标签噪声过滤 + 叙事归并」这一思路参考 ``jundizhou/easy-stock`` 的
``internal/narrative/narrative.go``（PolyForm Noncommercial 1.0.0，非 OSI 开源）。
本模块的**规则表与实现均为 AQSP 自研**，按 A 股语境重写，未复制其代码。

维护约定
--------
叙事表是**人工维护的分类学**，不是策略阈值（AGENTS §3.5 约束的是阈值，不是
分类学）。新增主线时追加 :class:`NarrativeRule`；规则按**声明顺序优先**匹配，
因此更具体的规则必须写在更宽泛的规则之前。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

LabelRole = Literal["noise", "structural", "narrative", "unmapped"]

ROLE_NOISE: LabelRole = "noise"
ROLE_STRUCTURAL: LabelRole = "structural"
ROLE_NARRATIVE: LabelRole = "narrative"
ROLE_UNMAPPED: LabelRole = "unmapped"


@dataclass(frozen=True)
class NarrativeRule:
    """一条叙事规则：叙事名 + 命中关键词（子串匹配，大小写不敏感）。"""

    name: str
    keywords: tuple[str, ...]


# ---------------------------------------------------------------------------
# 噪声标签：指数成分 / 交易机制 / 持仓属性 —— 不构成可交易叙事
# ---------------------------------------------------------------------------

NOISE_LABELS: frozenset[str] = frozenset(
    {
        # 交易机制 / 标的属性
        "融资融券",
        "转融券标的",
        "深股通",
        "沪股通",
        "标的证券",
        "标准普尔",
        "MSCI中国",
        "富时罗素",
        "标普道琼斯A股",
        "中证500_",
        "沪深300_",
        "上证180_",
        "深证100R",
        "深成500",
        "中证1000_",
        "创业板综",
        "上证50_",
        "科创50_",
        # 持仓属性 / 资金属性
        "QFII重仓",
        "基金重仓",
        "机构重仓",
        "社保重仓",
        "券商重仓",
        "信托重仓",
        "保险重仓",
        "北向资金",
        "外资持股",
        # 昨日反馈类（信息流里属于重复计数，不是新方向）
        "昨日涨停",
        "昨日连板",
        "昨日触板",
        "昨日涨停_含一字",
        "昨日连板_含一字",
        # 财务属性
        "破净股",
        "高股息",
        "低价股",
        "次新股",
        "预盈预增",
        "预亏预减",
        "股权转让",
    }
)

# 结构标签：地域 / 行政区划 —— 反映注册地，不反映资金方向
STRUCTURAL_LABELS: frozenset[str] = frozenset(
    {
        "长江三角",
        "深圳特区",
        "上海自贸",
        "海南自贸",
        "滨海新区",
        "成渝特区",
        "新疆振兴",
        "西藏振兴",
        "内蒙振兴",
        "东北振兴",
        "中部崛起",
        "西部大开发",
    }
)


# ---------------------------------------------------------------------------
# 叙事表（声明顺序 = 匹配优先级；具体规则在前）
# ---------------------------------------------------------------------------

NARRATIVE_RULES: tuple[NarrativeRule, ...] = (
    NarrativeRule(
        "光通信/CPO",
        ("CPO", "光通信模块", "光模块", "光芯片", "硅光", "光通信"),
    ),
    NarrativeRule(
        "AI算力",
        (
            "算力概念",
            "东数西算",
            "液冷服务器",
            "液冷概念",
            "AI服务器",
            "AI芯片",
            "华为昇腾",
            "英伟达概念",
            "数据中心",
            "边缘计算",
            "国资云",
            "超导概念",
        ),
    ),
    NarrativeRule(
        "AI应用",
        (
            "AI应用",
            "AI智能体",
            "多模态AI",
            "AIGC",
            "CHATGPT",
            "DEEPSEEK",
            "KIMI",
            "SORA",
            "智谱AI",
            "大模型",
            "AI语料",
            "生成式AI",
            "文心一言",
            "豆包",
            "AI手机",
            "AI眼镜",
            "AI医疗",
        ),
    ),
    NarrativeRule(
        "半导体",
        (
            "半导体概念",
            "国产芯片",
            "第三代半导体",
            "存储芯片",
            "先进封装",
            "芯片概念",
            "光刻机",
            "光刻胶",
            "中芯国际概念",
            "汽车芯片",
            "MCU芯片",
            "EDA概念",
            "半导体设备",
        ),
    ),
    NarrativeRule(
        "华为产业链",
        ("华为概念", "鸿蒙概念", "华为鲲鹏", "华为海思", "华为汽车", "华为欧拉"),
    ),
    NarrativeRule(
        "机器人",
        (
            "机器人概念",
            "人形机器人",
            "工业机器人",
            "机器视觉",
            "减速器",
            "灵巧手",
            "丝杠",
            "谐波减速器",
        ),
    ),
    NarrativeRule(
        "低空经济",
        ("低空经济", "飞行汽车", "无人机", "EVTOL"),
    ),
    NarrativeRule(
        "商业航天",
        ("商业航天", "卫星互联网", "航天航空", "卫星导航", "北斗导航"),
    ),
    NarrativeRule(
        "创新药",
        ("创新药", "减肥药", "AI制药", "CRO", "CXO", "生物医药", "医疗器械", "脑机接口"),
    ),
    NarrativeRule(
        "固态电池",
        ("固态电池", "钠离子电池", "电解质", "锂矿", "锂电池概念", "磷酸铁锂"),
    ),
    NarrativeRule(
        "储能",
        ("储能概念", "抽水蓄能", "熔盐储能", "虚拟电厂", "钠电池"),
    ),
    NarrativeRule(
        "新能源车",
        (
            "新能源汽车",
            "汽车零部件",
            "一体化压铸",
            "智能驾驶",
            "无人驾驶",
            "车路协同",
            "汽车电子",
        ),
    ),
    NarrativeRule(
        "光伏",
        (
            "光伏概念",
            "光伏设备",
            "HJT电池",
            "TOPCON电池",
            "钙钛矿电池",
            "BC电池",
            "逆变器",
        ),
    ),
    NarrativeRule("风电", ("风电概念", "海上风电", "风电设备")),
    NarrativeRule(
        "数据要素",
        ("数据要素", "数据确权", "数据安全", "数据交易", "数据资产"),
    ),
    NarrativeRule(
        "信创",
        ("信创", "国产软件", "操作系统", "国产替代", "网络安全", "密码安全"),
    ),
    NarrativeRule(
        "消费电子",
        (
            "消费电子",
            "苹果概念",
            "折叠屏",
            "面板",
            "MINILED",
            "智能穿戴",
            "无线耳机",
            "PCB",
        ),
    ),
    NarrativeRule("军工", ("军工概念", "国防军工", "大飞机", "军民融合", "航空发动机")),
    NarrativeRule("核电", ("核电概念", "核能核电", "可控核聚变", "核污染防治")),
    NarrativeRule("电力设备", ("智能电网", "特高压", "绿色电力", "电网设备")),
    NarrativeRule(
        "有色资源",
        ("稀土永磁", "小金属", "黄金概念", "铜缆高速连接", "铜", "铝", "钨", "钼", "锑"),
    ),
    NarrativeRule(
        "券商金融",
        ("券商概念", "参股券商", "互联网金融", "数字货币", "跨境支付", "金融科技"),
    ),
    NarrativeRule("农业", ("种业", "转基因", "粮食安全", "猪肉概念", "养殖", "水产养殖")),
    NarrativeRule(
        "央企改革",
        ("央企改革", "国企改革", "中特估", "国企混改", "并购重组", "借壳上市"),
    ),
    NarrativeRule(
        "化工周期",
        ("钛白粉", "磷化工", "氟化工", "纯碱", "煤炭", "石油", "化学原料"),
    ),
    NarrativeRule(
        "传媒娱乐",
        ("短剧游戏", "游戏", "影视院线", "IP经济", "谷子经济", "文化传媒", "出版"),
    ),
    NarrativeRule(
        "消费复苏",
        ("旅游酒店", "免税概念", "首发经济", "银发经济", "预制菜", "白酒", "零售"),
    ),
)


def normalize_label(label: object) -> str:
    """归一化标签：去空白（含全角空格）、统一大写用于匹配。

    ⚠️ 返回的是**匹配用**的大写形式；展示应保留原始 ``board_name``。
    """
    if label is None:
        return ""
    text = str(label)
    # 全角空格 / 不换行空格一并去掉，避免上游多一个空格就失配
    for blank in ("\u3000", "\u00a0"):
        text = text.replace(blank, "")
    return text.strip().upper()


def label_role(label: object) -> LabelRole:
    """判定标签角色：noise / structural / narrative / unmapped。"""
    normalized = normalize_label(label)
    if not normalized:
        return ROLE_UNMAPPED
    if normalized in _NOISE_UPPER:
        return ROLE_NOISE
    if normalized in _STRUCTURAL_UPPER:
        return ROLE_STRUCTURAL
    if _match_rule(normalized) is not None:
        return ROLE_NARRATIVE
    return ROLE_UNMAPPED


def narrative_for(label: object) -> str | None:
    """返回标签所属叙事名；噪声 / 结构 / 未映射一律返回 ``None``。"""
    normalized = normalize_label(label)
    if not normalized or normalized in _NOISE_UPPER or normalized in _STRUCTURAL_UPPER:
        return None
    rule = _match_rule(normalized)
    return rule.name if rule is not None else None


def is_noise_label(label: object) -> bool:
    """是否为噪声标签（指数成分 / 交易机制 / 持仓属性）。"""
    return normalize_label(label) in _NOISE_UPPER


def is_structural_label(label: object) -> bool:
    """是否为结构标签（地域 / 行政区划）。"""
    return normalize_label(label) in _STRUCTURAL_UPPER


def filter_labels(labels: Iterable[object]) -> tuple[str, ...]:
    """过滤掉噪声标签，保留原始写法与顺序（结构标签保留）。"""
    out: list[str] = []
    for label in labels:
        if not is_noise_label(label):
            raw = "" if label is None else str(label).strip()
            if raw:
                out.append(raw)
    return tuple(out)


def _match_rule(normalized: str) -> NarrativeRule | None:
    for rule in NARRATIVE_RULES:
        for keyword in rule.keywords:
            if keyword.upper() in normalized:
                return rule
    return None


@dataclass(frozen=True)
class NarrativeBucket:
    """归并后的一个叙事桶。"""

    name: str
    labels: tuple[str, ...]
    label_count: int
    mean_change_pct: float
    max_change_pct: float


def merge_narratives(
    pairs: Iterable[tuple[str, float]],
    *,
    include_unmapped: bool = True,
) -> tuple[NarrativeBucket, ...]:
    """把 ``(标签, 涨跌幅)`` 归并成叙事桶，按平均涨跌幅降序。

    噪声标签**始终丢弃**；结构标签默认计入 ``include_unmapped`` 的兜底桶
    （因为它们仍是可观察的板块异动，只是不属于任何交易主线）。

    空输入 ⇒ 空元组（不返回 ``None``，调用方可直接迭代）。
    """
    buckets: dict[str, list[tuple[str, float]]] = {}
    for raw_label, change_pct in pairs:
        label = "" if raw_label is None else str(raw_label).strip()
        if not label or is_noise_label(label):
            continue
        name = narrative_for(label)
        if name is None:
            if not include_unmapped:
                continue
            name = label
        buckets.setdefault(name, []).append((label, float(change_pct)))

    out: list[NarrativeBucket] = []
    for name, entries in buckets.items():
        values = [value for _, value in entries]
        out.append(
            NarrativeBucket(
                name=name,
                labels=tuple(label for label, _ in entries),
                label_count=len(entries),
                mean_change_pct=sum(values) / len(values),
                max_change_pct=max(values),
            )
        )
    out.sort(key=lambda bucket: bucket.mean_change_pct, reverse=True)
    return tuple(out)


_NOISE_UPPER = frozenset(normalize_label(item) for item in NOISE_LABELS)
_STRUCTURAL_UPPER = frozenset(normalize_label(item) for item in STRUCTURAL_LABELS)


__all__ = [
    "NARRATIVE_RULES",
    "NOISE_LABELS",
    "ROLE_NARRATIVE",
    "ROLE_NOISE",
    "ROLE_STRUCTURAL",
    "ROLE_UNMAPPED",
    "STRUCTURAL_LABELS",
    "LabelRole",
    "NarrativeBucket",
    "NarrativeRule",
    "filter_labels",
    "is_noise_label",
    "is_structural_label",
    "label_role",
    "merge_narratives",
    "narrative_for",
    "normalize_label",
]
