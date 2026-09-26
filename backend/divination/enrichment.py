# -*- coding: utf-8 -*-
"""ADR-DIV-001：Divination Failure Isolation（NON_BLOCKING）。

Divination 是 enrichment，**不是** safety dependency：

    Provider 不可用 / timeout   -> DEGRADED，无侧注，告警
    Contract / manifest mismatch -> 拒绝侧注 + 告警，安全流程不受影响
    Divination malformed result  -> 拒绝侧注 + 告警，安全流程不受影响
    Engine 本身不可用            -> 与本模块无关，沿用 P0-B fail-closed

无论哪种情况，**绝不**：
    - 改变 Safety Decision（level / CRISIS / SAFETY_PLAN / STABILIZE / REPAIR）
    - 绕过或削弱 Meta-Arbiter
    - 阻断安全流程

本模块所有 public 方法都返回 ``DivinationResult``（失败时 degraded=True），
**不抛异常给安全路径**——调用方无需 try/except 也不会因占星服务故障而中断。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from .types import (
    SYMBOLIC_CONFIDENCE_MIN,
    DivinationResult,
    SymbolicAnnotation,
)

logger = logging.getLogger("heartmirror.divination")

#: 安全事件码：供监控告警消费
SECURITY_EVENT_PROVIDER_UNAVAILABLE = "DIVINATION_PROVIDER_UNAVAILABLE"
SECURITY_EVENT_CONTRACT_MISMATCH = "DIVINATION_CONTRACT_MISMATCH"
SECURITY_EVENT_MALFORMED = "DIVINATION_MALFORMED_RESULT"


class DivinationEnrichment:
    """把占星推演作为**可选侧注**附加到对话，不参与任何安全裁决。

    依赖注入 ``provider_fn(domain) -> Mapping`` 而非直接持有 DivinationClient：
      1. 本模块不 import 跨仓包（``divination_consumer`` 与 ``engine/`` 同级，
         需由调用方在已配置 sys.path 的上下文里构造并注入）；
      2. 便于测试：注入可控 provider，无需真实 HTTP。
    """

    def __init__(
        self,
        provider_fn: Callable[[str], Mapping[str, Any]] | None = None,
        *,
        domain: str = "liuyao",
        security_sink: Callable[[str, str], None] | None = None,
    ) -> None:
        self._provider_fn = provider_fn
        self._domain = domain
        self._security_sink = security_sink

    # ------------------------------------------------------------------ 诊断
    def _emit(self, event: str, detail: str) -> None:
        """上报可观测诊断。**绝不**影响安全行为。"""
        logger.warning("[%s] %s", event, detail)
        if self._security_sink is not None:
            try:
                self._security_sink(event, detail)
            except Exception:  # 诊断链路自身故障不得外溢
                logger.exception("divination security_sink 异常（已忽略）")

    # ------------------------------------------------------------------ 解析
    @staticmethod
    def _extract_symbols(raw: Mapping[str, Any]) -> tuple[SymbolicAnnotation, ...]:
        """从 Provider 响应中提取象征侧注。

        只读取显式侧注字段；任何带安全评分语义的键（score/level/risk）一律丢弃
        ——即使 Provider 误发，也不允许其进入象征层。
        """
        symbols: list[SymbolicAnnotation] = []
        for item in raw.get("symbols", []) or []:
            if not isinstance(item, Mapping):
                continue
            if any(k in item for k in ("score", "level", "risk", "riskLevel", "lethal")):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            symbols.append(
                SymbolicAnnotation(
                    domain=str(item.get("domain", "")),
                    engine=str(item.get("engine", "")),
                    content=content,
                    confidence=item.get("confidence", SYMBOLIC_CONFIDENCE_MIN),
                    provenance=str(item.get("provenance", "")),
                )
            )
        return tuple(symbols)

    # ------------------------------------------------------------------ 主入口
    def enrich(self, text: str) -> DivinationResult:
        """取回象征侧注。**任何失败都不抛异常、不影响安全流程。**"""
        if self._provider_fn is None:
            return DivinationResult(
                domain=self._domain, degraded=True,
                reason="provider 未配置（占星增强未启用）",
            )

        try:
            raw = self._provider_fn(self._domain)
        except Exception as exc:
            event, code = self._classify(exc)
            self._emit(
                event,
                f"domain={self._domain} type={type(exc).__name__} detail={exc}",
            )
            return DivinationResult(
                domain=self._domain, degraded=True,
                reason=f"{code}: {type(exc).__name__}",
            )

        if not isinstance(raw, Mapping):
            self._emit(
                SECURITY_EVENT_MALFORMED,
                f"domain={self._domain} 响应类型非法: {type(raw).__name__}",
            )
            return DivinationResult(
                domain=self._domain, degraded=True,
                reason="malformed: 非 mapping 响应",
            )

        try:
            symbols = self._extract_symbols(raw)
        except Exception as exc:
            self._emit(
                SECURITY_EVENT_MALFORMED,
                f"domain={self._domain} 解析失败: {type(exc).__name__}: {exc}",
            )
            return DivinationResult(
                domain=self._domain, degraded=True,
                reason=f"malformed: {type(exc).__name__}",
            )

        if not symbols:
            return DivinationResult(
                domain=self._domain, degraded=True,
                reason="无有效象征侧注（已丢弃疑似安全评分字段）",
            )

        return DivinationResult(
            domain=self._domain,
            symbols=symbols,
            provenance=str(raw.get("provenance", "")),
        )

    @staticmethod
    def _classify(exc: Exception) -> tuple[str, str]:
        """把 Provider 异常分类为故障域（决定诊断事件，不决定安全行为）。"""
        name = type(exc).__name__
        if name in ("ProviderUnavailableError", "ConnectError", "TimeoutException", "ReadTimeout"):
            return SECURITY_EVENT_PROVIDER_UNAVAILABLE, "provider_unavailable"
        if name in (
            "ManifestValidationError", "SchemaVersionMismatch",
            "RuleIntegrityError", "DivinationAPIError",
        ):
            return SECURITY_EVENT_CONTRACT_MISMATCH, "contract_mismatch"
        return SECURITY_EVENT_PROVIDER_UNAVAILABLE, "provider_error"

    # ------------------------------------------------------------------ 呈现
    @staticmethod
    def render_annotation(result: DivinationResult) -> str:
        """把侧注渲染为提示词片段。

        仅供 presentation / 上下文增强；调用方**不得**把返回文本送入任何
        参与安全裁决的通道。
        """
        if not result.is_usable:
            return ""
        return "\n\n".join(s.as_prompt_suffix() for s in result.to_annotations())
