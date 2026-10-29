"""Safe model contract failures; provider text is never an error message."""
from __future__ import annotations

from typing import Literal

ContractIssue = Literal[
    'action_numbers', 'action_evidence', 'embedded_reference', 'evidence_reference',
    'price_reference', 'condition_reference', 'peer_reference', 'hard_constraint', 'route_contract',
]

_MESSAGES: dict[ContractIssue, str] = {
    'action_numbers': '模型动作的数字条件只能引用当前有源价位；股数、费用和历史统计须留在解释中，本次未采用该意见。',
    'action_evidence': '动作中的历史统计或价位身份与当前来源不符；不能据此建立买卖条件，本次未采用该意见。',
    'embedded_reference': '动作说明混入了引用ID；引用须放在对应字段，本次未采用该意见。',
    'evidence_reference': '模型引用了本次未提供的证据，本次未采用该意见。',
    'price_reference': '买卖观察缺少有效来源价位，或引用了本次未提供的价位，本次未采用该意见。',
    'condition_reference': '模型引用了未提供、重复或矛盾的可监控条件，本次未采用该意见。',
    'peer_reference': '模型虚构了同行，或未回应本次实际提供的同行意见，本次未采用该意见。',
    'hard_constraint': '模型动作与当前可卖股份、行情有效性或风险暂停条件冲突，本次未采用该意见。',
    'route_contract': '模型未按要求完整比较三条路线，本次未采用该汇总。',
}


class ModelContractError(ValueError):
    """A fixed reason code and safe explanation, independent of raw output."""

    def __init__(self, code: ContractIssue) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])
