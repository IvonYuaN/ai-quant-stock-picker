"""Cross-market implication rules and runtime summary.

Extracted from ``market_context.py`` to isolate the rule definitions,
runtime summary helpers, and the deterministic implication rule table.
The implication *computation* functions remain in ``market_context.py``
because they are tightly coupled with the artifact builder and pick context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aqsp.goal_switches import goal_switch_enabled
from aqsp.news.catalysts import Impact


@dataclass(frozen=True)
class CrossMarketImplicationRule:
    rule_id: str
    keywords: tuple[str, ...]
    theme: str
    linkage_basis: str
    supportive_impacts: tuple[Impact, ...]
    a_share_targets: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    relevance_keywords: tuple[str, ...]
    lead_window: str
    observation_window: str
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    confirmation_hint: str
    required_keyword_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class CrossMarketImplication:
    rule_id: str
    theme: str
    linkage_basis: str
    a_share_targets: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    relevance_keywords: tuple[str, ...]
    lead_window: str
    observation_window: str
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    confirmation_hint: str
    strength: Literal["强", "中", "弱"]
    action: str
    source_title: str
    source_category: str
    source_quality_label: str
    source_quality_score: int
    source_published_at: str
    support_event_count: int
    conflict_event_count: int
    evidence_stack_summary: str
    evidence_points: tuple[str, ...]
    summary_line: str
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    confidence: float = 0.0
    time_horizon: str = ""
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    source_regions: tuple[str, ...] = ()
    impact_direction: Literal["positive", "negative", "mixed", "neutral"] = "neutral"
    source_url: str = ""
    source_fetched_at: str = ""


@dataclass(frozen=True)
class CrossMarketRuleRuntimeSummary:
    domestic_enabled: bool
    global_enabled: bool
    rule_count: int
    core_rule_ids: tuple[str, ...]
    rule_themes: tuple[str, ...]
    advisory_boundary: str


_CROSS_MARKET_RULES: tuple[CrossMarketImplicationRule, ...] = (
    CrossMarketImplicationRule(
        rule_id="commercial_space",
        keywords=(
            "spacex",
            "space x",
            "starlink",
            "星链",
            "商业航天",
            "卫星",
            "火箭",
            "发射",
            "低轨",
        ),
        required_keyword_groups=(
            (
                "spacex",
                "space x",
                "starlink",
                "星链",
                "商业航天",
                "卫星",
                "火箭",
                "低轨",
            ),
        ),
        theme="海外商业航天催化",
        linkage_basis="题材映射",
        supportive_impacts=("positive",),
        a_share_targets=("商业航天", "卫星互联网", "军工电子"),
        first_order_targets=("商业航天龙头", "卫星互联网/低轨组网", "火箭发射配套"),
        second_order_targets=("军工电子", "通信设备", "高端制造"),
        pressure_targets=(),
        execution_watchpoints=(
            "商业航天龙头竞价强度与换手承接",
            "卫星互联网扩散家数",
            "军工电子和通信设备是否跟随补涨",
        ),
        relevance_keywords=("航天", "卫星", "军工", "火箭", "低轨", "通信设备"),
        lead_window="隔夜-2日",
        observation_window="2-5日",
        transmission_path=(
            "SpaceX IPO、估值融资或重大发射先抬升海外商业航天风险偏好",
            "A股商业航天、卫星互联网与低轨组网龙头先反应",
            "若扩散到军工电子、通信设备和高端制造，持续性更强",
        ),
        validation_signals=(
            "商业航天龙头高开后仍有放量换手承接",
            "卫星互联网、低轨组网与火箭配套同步扩散",
            "军工电子出现跟随补涨而非单点脉冲",
        ),
        invalidation_signals=(
            "只有 SpaceX 新闻刺激但A股商业航天家数不扩散",
            "龙头高开低走且换手承接衰减",
            "主线资金迅速切回别的热门题材",
        ),
        confirmation_hint="先看商业航天龙头换手承接、卫星链扩散和军工电子跟随。",
    ),
    CrossMarketImplicationRule(
        rule_id="domestic_policy_stimulus",
        keywords=(
            "国常会",
            "发改委",
            "工信部",
            "专项债",
            "财政政策",
            "财政加力",
            "稳增长",
            "设备更新",
            "以旧换新",
            "低空经济",
            "消费刺激",
            "政策细则",
            "地方跟进",
        ),
        required_keyword_groups=(
            (
                "设备更新",
                "工业母机",
                "机器人",
                "自动化",
                "以旧换新",
                "家电",
                "汽车",
                "低空经济",
                "专项债",
                "财政政策",
                "财政加力",
                "稳增长",
                "消费刺激",
                "基建",
            ),
        ),
        theme="国内政策催化",
        linkage_basis="政策预期差映射",
        supportive_impacts=("positive",),
        a_share_targets=("设备更新", "低空经济", "汽车家电", "基建链", "机器人"),
        first_order_targets=("设备更新", "低空经济", "汽车家电以旧换新"),
        second_order_targets=("工业母机/机器人", "工程机械", "基建链", "充电桩"),
        pressure_targets=("纯防御高股息",),
        execution_watchpoints=(
            "政策受益龙头竞价强度与封单/换手承接",
            "设备更新、低空经济和以旧换新是否扩散",
            "地方细则或部委后续文件是否继续跟进",
        ),
        relevance_keywords=(
            "设备更新",
            "工业母机",
            "机器人",
            "自动化",
            "低空经济",
            "家电",
            "汽车",
            "工程机械",
            "基建",
            "充电桩",
            "消费",
        ),
        lead_window="当日-次日",
        observation_window="1-5日",
        transmission_path=(
            "国常会、部委或财政政策释放稳增长和产业支持预期",
            "A股设备更新、低空经济、汽车家电以旧换新先获得映射资金",
            "若地方细则和资金安排跟进，行情从龙头扩散到工程机械、机器人和基建链",
        ),
        validation_signals=(
            "政策受益龙头竞价强且开盘后仍有换手承接",
            "设备更新、低空经济、汽车家电至少两个方向同步扩散",
            "部委细则、地方方案或资金安排继续跟进",
        ),
        invalidation_signals=(
            "只有口号没有细则或资金安排",
            "龙头高开低走且板块家数不扩散",
            "资金仍停留在防御方向，政策线未形成风险偏好切换",
        ),
        confirmation_hint="先看政策受益龙头承接，再看细则跟进和板块扩散。",
    ),
    CrossMarketImplicationRule(
        rule_id="physical_ai",
        keywords=(
            "英伟达",
            "nvidia",
            "physical ai",
            "物理ai",
            "具身",
            "机器人",
            "humanoid",
            "embodied",
        ),
        required_keyword_groups=(
            (
                "physical ai",
                "物理ai",
                "具身",
                "humanoid",
                "embodied",
            ),
        ),
        theme="海外物理AI叙事升温",
        linkage_basis="产业映射",
        supportive_impacts=("positive",),
        a_share_targets=("机器人", "AI算力", "传感器", "丝杠", "减速器", "工控链"),
        first_order_targets=("机器人整机", "AI算力/边缘计算", "丝杠/减速器", "传感器"),
        second_order_targets=("工控", "机器视觉", "伺服", "算力芯片"),
        pressure_targets=(),
        execution_watchpoints=(
            "机器人龙头放量强度",
            "丝杠减速器是否同步走强",
            "是否有订单或产业催化继续跟进",
        ),
        relevance_keywords=(
            "机器人",
            "传感器",
            "丝杠",
            "减速器",
            "工控",
            "算力",
            "ai芯片",
            "边缘计算",
            "自动化",
            "具身",
            "机器视觉",
            "伺服",
        ),
        lead_window="隔夜-3日",
        observation_window="2-5日",
        transmission_path=(
            "海外大厂发布 Physical AI 或具身新平台",
            "A股机器人、边缘算力与核心零部件先获得映射资金",
            "若订单逻辑、算力链与工控链共振，主题可延续数日",
        ),
        validation_signals=(
            "机器人龙头放量上攻且核心零部件同步走强",
            "AI算力或边缘计算分支同步放量扩散",
            "丝杠减速器传感器不是单一分支独涨",
            "盘中有产业催化或订单消息继续验证",
        ),
        invalidation_signals=(
            "只有海外叙事但A股机器人板块不共振",
            "映射只停留在高开冲动，午后承接消失",
            "零部件和整机分化严重，难形成主线接力",
        ),
        confirmation_hint="优先看有订单、放量和产业催化验证的环节。",
    ),
    CrossMarketImplicationRule(
        rule_id="us_risk_on",
        keywords=(
            "美股大涨",
            "纳斯达克",
            "nasdaq",
            "标普",
            "spx",
            "风险资产反弹",
            "科技股反弹",
            "risk-on",
        ),
        required_keyword_groups=(
            ("美股", "纳斯达克", "nasdaq", "标普", "spx", "科技股", "风险资产"),
            ("大涨", "反弹", "修复", "走强", "risk-on"),
        ),
        theme="外盘风险偏好修复",
        linkage_basis="风险偏好映射",
        supportive_impacts=("positive",),
        a_share_targets=("成长", "高弹性", "AI链"),
        first_order_targets=("AI链高弹性", "算力/芯片", "机器人成长"),
        second_order_targets=("软件", "半导体设备", "科创弹性标的"),
        pressure_targets=("高股息防御",),
        execution_watchpoints=(
            "次日竞价成长方向是否强于防御",
            "北向回流力度",
            "科技权重承接与量能是否同步",
        ),
        relevance_keywords=(
            "成长",
            "高弹性",
            "ai",
            "人工智能",
            "科技",
            "算力",
            "芯片",
            "半导体",
            "软件",
            "机器人",
        ),
        lead_window="次日竞价-1日",
        observation_window="次日-3日",
        transmission_path=(
            "美股科技与风险资产先修复风险偏好",
            "A股高弹性成长与AI链在竞价和早盘先反馈",
            "若北向回流并伴随量能放大，修复可延续到2-3日",
        ),
        validation_signals=(
            "次日竞价高弹性方向明显强于防御方向",
            "北向资金回流且科技权重承接稳定",
            "AI链和高弹性成长出现板块级放量",
        ),
        invalidation_signals=(
            "美股强但A股竞价无明显风险偏好跟随",
            "北向继续流出导致开盘后快速回落",
            "高弹性方向仅个股脉冲，缺少板块扩散",
        ),
        confirmation_hint="优先看竞价情绪、北向反馈和高弹性方向承接。",
    ),
    CrossMarketImplicationRule(
        rule_id="global_liquidity_easing",
        keywords=(
            "降息",
            "降息预期升温",
            "降息交易",
            "美债收益率下行",
            "美债利率下行",
            "美元走弱",
            "美联储鸽派",
            "鸽派表态",
            "rate cut",
            "dovish",
            "treasury yields fall",
            "dollar weakens",
        ),
        required_keyword_groups=(),
        theme="全球流动性宽松交易",
        linkage_basis="贴现率与风险偏好映射",
        supportive_impacts=("positive",),
        a_share_targets=("成长", "AI链", "黄金", "有色金属", "创新药"),
        first_order_targets=("高弹性成长", "AI链/算力", "黄金/有色"),
        second_order_targets=("创新药", "港股映射", "券商弹性"),
        pressure_targets=("银行息差", "高股息防御"),
        execution_watchpoints=(
            "成长和AI链竞价是否强于防御",
            "黄金有色是否跟随美元走弱同步走强",
            "银行与高股息是否相对承压",
        ),
        relevance_keywords=(
            "成长",
            "ai",
            "人工智能",
            "算力",
            "芯片",
            "半导体",
            "黄金",
            "有色",
            "贵金属",
            "创新药",
            "券商",
        ),
        lead_window="隔夜-2日",
        observation_window="1-3日",
        transmission_path=(
            "美联储鸽派、降息交易或美债收益率下行先改善全球流动性预期",
            "A股高弹性成长、AI链与黄金有色先获得估值和商品双重映射",
            "若银行高股息相对承压且北向回流，流动性交易更容易延续",
        ),
        validation_signals=(
            "成长和AI链竞价强于高股息防御",
            "黄金有色跟随美元走弱和美债收益率下行同步放量",
            "北向资金回流且科技权重承接稳定",
        ),
        invalidation_signals=(
            "降息交易未传导到A股，成长方向竞价弱于防御",
            "美元或美债收益率反向走强，黄金有色冲高回落",
            "银行高股息继续强于成长，说明市场仍在防御定价",
        ),
        confirmation_hint="先看成长/AI链相对强度，再看黄金有色和北向资金是否共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="chip_export_controls",
        keywords=(
            "出口管制",
            "禁售",
            "实体清单",
            "断供",
            "关税",
            "tariff",
            "export control",
            "entity list",
            "sanction",
            "restriction",
        ),
        required_keyword_groups=(
            (
                "芯片",
                "半导体",
                "算力",
                "gpu",
                "h20",
                "h100",
                "先进制程",
                "光刻",
                "eda",
                "服务器",
                "server",
                "ai",
            ),
        ),
        theme="海外芯片限制升级",
        linkage_basis="供应链重定价",
        supportive_impacts=("negative",),
        a_share_targets=("半导体设备", "半导体材料", "国产算力", "信创"),
        first_order_targets=("半导体设备", "半导体材料", "国产算力"),
        second_order_targets=("EDA/IP", "先进封装", "军工电子"),
        pressure_targets=("苹果链", "出口代工"),
        execution_watchpoints=(
            "半导体设备与国产算力是否同步放量",
            "苹果链与出口代工是否明显承压",
            "自主可控是否从设备扩散到材料与信创",
        ),
        relevance_keywords=(
            "半导体",
            "芯片",
            "设备",
            "材料",
            "国产算力",
            "信创",
            "先进封装",
            "eda",
            "ip",
            "军工电子",
        ),
        lead_window="隔夜-3日",
        observation_window="2-5日",
        transmission_path=(
            "海外芯片限制或关税升级先扰动全球科技供应链预期",
            "A股半导体设备材料与国产算力先获得自主可控映射资金",
            "若苹果链与出口代工承压，自主可控主线延续性更强",
        ),
        validation_signals=(
            "半导体设备材料与国产算力同步放量而非单点脉冲",
            "自主可控从设备扩散到材料、先进封装或信创",
            "苹果链与出口代工承压，说明资金完成切换",
        ),
        invalidation_signals=(
            "只有消息刺激但半导体设备材料不扩散",
            "自主可控高开后快速回落，苹果链并未承压",
            "市场把消息仅当情绪噪音，未形成板块级共振",
        ),
        confirmation_hint="先看设备材料与国产算力是否同步共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="global_supply_tightening",
        keywords=(
            "涨价",
            "提价",
            "报价上调",
            "缺货",
            "供不应求",
            "供给收缩",
            "停产",
            "限产",
            "库存低位",
        ),
        required_keyword_groups=(
            (
                "dram",
                "nand",
                "hbm",
                "wafer",
                "panel",
                "memory",
                "存储",
                "半导体",
                "芯片",
                "封装",
                "pcb",
                "覆铜板",
                "面板",
            ),
        ),
        theme="海外供给收缩映射",
        linkage_basis="供需缺口映射",
        supportive_impacts=("positive",),
        a_share_targets=("存储", "半导体材料", "先进封装", "PCB"),
        first_order_targets=("存储", "半导体材料", "先进封装"),
        second_order_targets=("PCB", "覆铜板", "面板"),
        pressure_targets=("消费电子代工", "下游整机"),
        execution_watchpoints=(
            "存储与半导体材料是否同步放量",
            "先进封装与PCB是否出现扩散",
            "消费电子代工和下游整机是否承压",
        ),
        relevance_keywords=(
            "存储",
            "半导体材料",
            "先进封装",
            "封装",
            "pcb",
            "覆铜板",
            "面板",
            "消费电子",
        ),
        lead_window="隔夜-2日",
        observation_window="2-5日",
        transmission_path=(
            "海外供给收缩或涨价先抬升相关原件与材料报价预期",
            "A股存储、半导体材料与先进封装先获得映射资金",
            "若扩散到PCB覆铜板且消费电子承压，主题持续性更强",
        ),
        validation_signals=(
            "存储与半导体材料同步放量而非单一环节独涨",
            "先进封装与PCB出现扩散，说明成本传导被市场认可",
            "消费电子代工与下游整机承压，资金切向上游弹性",
        ),
        invalidation_signals=(
            "只有消息刺激但存储材料不扩散",
            "上游高开后快速回落，消费电子链并未承压",
            "市场把涨价消息当成短脉冲，缺少板块级共振",
        ),
        confirmation_hint="先看上游材料、存储与封装是否一起共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="oil_price_shock",
        keywords=(
            "油价大涨",
            "油价飙升",
            "原油大涨",
            "原油价格上涨",
            "布伦特原油",
            "wti",
            "brent",
            "crude oil",
            "opec",
            "减产",
            "原油供应",
            "能源价格",
        ),
        required_keyword_groups=(),
        theme="国际油价冲击",
        linkage_basis="商品价格与成本映射",
        supportive_impacts=("positive", "negative"),
        a_share_targets=("油气", "煤化工", "航运", "资源品"),
        first_order_targets=("油气开采", "油服", "煤炭/煤化工"),
        second_order_targets=("航运", "资源品", "通胀受益链"),
        pressure_targets=("航空", "下游化工", "消费运输"),
        execution_watchpoints=(
            "油气和油服是否同步放量",
            "航空与下游化工是否相对承压",
            "煤炭煤化工是否跟随能源价格扩散",
        ),
        relevance_keywords=(
            "油气",
            "石油",
            "油服",
            "煤炭",
            "煤化工",
            "航运",
            "资源品",
            "航空",
            "化工",
            "能源",
        ),
        lead_window="当日-次日",
        observation_window="1-3日",
        transmission_path=(
            "国际油价或OPEC减产消息先改变能源价格预期",
            "A股油气、油服和煤化工先获得价格弹性映射",
            "若航空与下游化工承压，资金更容易向上游能源链集中",
        ),
        validation_signals=(
            "油气和油服同步放量而非单一龙头脉冲",
            "煤炭煤化工跟随走强，能源链形成扩散",
            "航空和下游化工相对承压，说明成本传导被市场定价",
        ),
        invalidation_signals=(
            "油价冲高回落或减产预期被证伪",
            "A股油气只有高开冲动，油服煤化工不扩散",
            "航空和下游化工不承压，说明成本压力未被交易",
        ),
        confirmation_hint="先看油气油服共振，再看煤化工扩散和航空化工承压。",
    ),
    CrossMarketImplicationRule(
        rule_id="geopolitics",
        keywords=(
            "打仗",
            "战争",
            "冲突",
            "袭击",
            "中东",
            "停火破裂",
            "地缘",
            "middle east",
            "geopolitical",
            "geopolitical risk",
            "military",
            "attack",
            "war",
        ),
        required_keyword_groups=(
            (
                "打仗",
                "战争",
                "冲突",
                "袭击",
                "中东",
                "停火破裂",
                "地缘",
                "middle east",
                "geopolitical",
                "geopolitical risk",
                "military",
                "attack",
                "war",
            ),
            (
                "黄金",
                "贵金属",
                "军工",
                "油气",
                "能源",
                "避险",
                "原油",
                "航运",
                "资源品",
                "gold",
                "precious metals",
                "defense",
                "defence",
                "safe haven",
                "oil",
                "crude",
                "energy",
            ),
        ),
        theme="地缘冲突升温",
        linkage_basis="避险定价映射",
        supportive_impacts=("negative",),
        a_share_targets=("黄金", "军工", "能源链"),
        first_order_targets=("黄金", "军工", "油气"),
        second_order_targets=("航运", "资源品"),
        pressure_targets=("高beta成长", "风险偏好题材"),
        execution_watchpoints=(
            "黄金军工油气是否至少两个方向共振",
            "成长高beta是否明显承压",
            "外盘避险资产或商品价格是否继续强化",
        ),
        relevance_keywords=(
            "黄金",
            "贵金属",
            "军工",
            "油气",
            "能源",
            "航运",
            "gold",
            "defense",
            "defence",
            "safe haven",
            "oil",
            "energy",
        ),
        lead_window="当日-次日",
        observation_window="1-3日",
        transmission_path=(
            "地缘冲突先抬升避险与资源品定价",
            "A股黄金军工油气先成为情绪承接方向",
            "若成长承压且避险链扩散，短线持续性提升",
        ),
        validation_signals=(
            "黄金军工油气三个方向至少两个同步走强",
            "成长高beta开盘承压，资金明显切向避险链",
            "商品价格或海外避险资产继续强化",
        ),
        invalidation_signals=(
            "消息很快降温或停火预期回升",
            "A股避险链只有单一板块脉冲",
            "成长方向未受压制，说明资金未完成切换",
        ),
        confirmation_hint="先看避险链强度，再防范成长和高beta承压。",
    ),
)


def cross_market_rule_runtime_summary(
    *,
    enable_domestic_intelligence: bool | None = None,
    enable_global_intelligence: bool | None = None,
) -> CrossMarketRuleRuntimeSummary:
    domestic_enabled = (
        enable_domestic_intelligence
        if enable_domestic_intelligence is not None
        else goal_switch_enabled("domestic_market_intelligence", default=True)
    )
    global_enabled = (
        enable_global_intelligence
        if enable_global_intelligence is not None
        else goal_switch_enabled("global_market_intelligence", default=True)
    )
    rules_by_id = {rule.rule_id: rule for rule in _CROSS_MARKET_RULES}
    core_rule_ids = tuple(
        rule_id
        for rule_id in (
            "commercial_space",
            "physical_ai",
            "geopolitics",
            "us_risk_on",
            "global_liquidity_easing",
            "oil_price_shock",
        )
        if rule_id in rules_by_id
    )
    rule_themes = tuple(rules_by_id[rule_id].theme for rule_id in core_rule_ids)
    boundary = (
        "deterministic_context_priority_only"
        if global_enabled
        else "global_market_intelligence_disabled"
    )
    return CrossMarketRuleRuntimeSummary(
        domestic_enabled=domestic_enabled,
        global_enabled=global_enabled,
        rule_count=len(_CROSS_MARKET_RULES) if global_enabled else 0,
        core_rule_ids=core_rule_ids if global_enabled else (),
        rule_themes=rule_themes if global_enabled else (),
        advisory_boundary=boundary,
    )


def cross_market_rule_runtime_lines() -> tuple[str, ...]:
    summary = cross_market_rule_runtime_summary()
    return (
        f"- market_context_domestic_enabled: {summary.domestic_enabled}",
        f"- market_context_global_enabled: {summary.global_enabled}",
        f"- cross_market_rule_count: {summary.rule_count}",
        f"- cross_market_core_rules: {','.join(summary.core_rule_ids) or '-'}",
        f"- cross_market_rule_themes: {'；'.join(summary.rule_themes) or '-'}",
        f"- cross_market_boundary: {summary.advisory_boundary}",
    )
