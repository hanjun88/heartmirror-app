# -*- coding: utf-8 -*-
"""ADR-DIV-002：象征数据隔离（类型层，主防线）。

核心不变量（类型层强制，非约定）：

    DivinationResult ∉ SafetyInput
    SymbolicAnnotation ∉ SafetyDecision
    symbolic confidence ∉ lethal_score / arousal / level / escalation

设计要点
--------
1. ``SafetyInput`` 是 Meta-Arbiter 唯一合法输入，**结构上不含**任何 divination /
   symbolic 字段。推演数据在类型层面就无法传入安全裁决。
2. ``DivinationResult`` / ``SymbolicAnnotation`` 是**独立类型**，只允许流向
   presentation / context enrichment。
3. 刻意不提供 ``arbiter.evaluate(..., metadata=divination_result)`` 这类旁路：
   metadata 参数最终极易成为隐式泄漏通道。
4. ``to_engine_kwargs()`` 是 SafetyInput → 引擎调用的**唯一**转换出口，
   输出键集合是白名单（additive 不会自动带入新字段）。

ADR-DIV-001：Divination 是 enrichment，**不是** safety dependency。
本模块任何类型都不参与安全决策，也不具备阻断安全流程的能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# 象征层置信硬锁（与引擎侧 SYMBOLIC_CONFIDENCE_LOCK 一致，见 engine/liuyao_engine.py）
# 象征层置信永远停留在低区间，不得抬升到安全判定所需的证据强度。
# ---------------------------------------------------------------------------
SYMBOLIC_CONFIDENCE_MIN = 0.36
SYMBOLIC_CONFIDENCE_MAX = 0.40

#: 运行时守卫用的禁止字段名（小写匹配）。出现在 SafetyInput 即视为违规。
FORBIDDEN_SAFETY_FIELDS: frozenset[str] = frozenset({
    "divination",
    "divination_result",
    "symbolic",
    "symbolic_profiles",
    "symbolic_score",
    "symbolic_confidence",
    "symbolic_annotation",
    "symbolic_annotations",
    "bazi",
    "bazi_chart",
    "ziwei",
    "ziwei_chart",
    "astrology",
    "western_natal",
    "vedic",
    "vedic_d9",
    "liuyao",
    "natal",
    "chart",
    "metadata",
})


class SymbolicLockViolation(RuntimeError):
    """检测到象征数据试图进入安全裁决路径。

    注意：触发时**不**停止安全引擎。按 ADR-DIV-001，调用方应丢弃象征数据、
    保留原始 SafetyInput 继续安全流程，并上报可观测诊断。
    """


def _coerce_confidence(raw: Any, *, domain: str) -> float:
    """把象征置信度夹到硬锁区间，绝不放行越界值。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = SYMBOLIC_CONFIDENCE_MIN
    if value != value:  # NaN
        value = SYMBOLIC_CONFIDENCE_MIN
    return max(SYMBOLIC_CONFIDENCE_MIN, min(SYMBOLIC_CONFIDENCE_MAX, value))


@dataclass(frozen=True)
class SymbolicAnnotation:
    """象征层侧注：只允许进入 presentation / 上下文增强。

    ``confidence`` 构造时即被夹到 [0.36, 0.40]，调用方无法传入越界值。
    """

    domain: str
    engine: str
    content: str
    confidence: float = SYMBOLIC_CONFIDENCE_MIN
    provenance: str = ""

    def __post_init__(self) -> None:
        clamped = _coerce_confidence(self.confidence, domain=self.domain)
        if clamped != self.confidence:
            # frozen dataclass：用 object.__setattr__ 完成构造期收敛
            object.__setattr__(self, "confidence", clamped)

    def as_prompt_suffix(self) -> str:
        """渲染为提示词侧注片段。

        仅可拼接到 LLM 上下文的**独立侧注区**，不得参与任何评分/裁决计算。
        措辞刻意保持"参考性"而非"判定性"，避免侧注被当作证据使用。
        """
        head = f"[象征层参考 · {self.domain} · 置信 {self.confidence:.2f} · 非安全判定依据]"
        return f"{head}\n{self.content}" if self.content else head


@dataclass(frozen=True)
class DivinationResult:
    """推演结果容器。**禁止**传入 Meta-Arbiter / SafetyInput。"""

    domain: str
    symbols: tuple[SymbolicAnnotation, ...] = ()
    provenance: str = ""
    degraded: bool = False
    reason: str = ""

    @property
    def is_usable(self) -> bool:
        return bool(self.symbols) and not self.degraded

    def to_annotations(self) -> tuple[SymbolicAnnotation, ...]:
        return self.symbols if self.is_usable else ()


@dataclass(frozen=True)
class SafetyInput:
    """安全裁决唯一合法输入。

    结构上**不含** divination / symbolic 字段——类型层即隔离，而非靠调用方自觉。
    ``to_engine_kwargs()`` 是通往引擎调用的唯一出口，采用白名单而非黑名单。
    """

    user_message: str
    conversation_context: str = ""
    behavioral_signals: tuple[str, ...] = ()
    safety_features: Mapping[str, float] = field(default_factory=dict)

    #: 显式白名单：新增字段不会自动泄漏到引擎调用
    _ENGINE_KWARG_WHITELIST: tuple[str, ...] = ()

    def to_engine_kwargs(self) -> dict[str, Any]:
        """转换为引擎调用参数（仅 user_message / context）。"""
        kwargs: dict[str, Any] = {"text": self.user_message}
        if self.conversation_context:
            kwargs["conversation_context"] = self.conversation_context
        if self.behavioral_signals:
            kwargs["behavioral_signals"] = tuple(self.behavioral_signals)
        if self.safety_features:
            kwargs["safety_features"] = dict(self.safety_features)
        return kwargs

    def field_names(self) -> frozenset[str]:
        return frozenset(self.__dataclass_fields__) - {"_ENGINE_KWARG_WHITELIST"}
