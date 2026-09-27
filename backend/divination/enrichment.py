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

P5a 双源链路
============
``enrich(text, user=None)``：

* ``user is None`` —— 旧单源路径（P4 / ADR-DIV 既有行为）：仅调
  ``provider_fn(domain)``，保持向后兼容。
* ``user`` 携带出生信息 —— 双源路径：
  - 源 A（引擎仓历法）：``engine_bazi_fn(dt_local, sex)`` 取干支八字；
    若同时有经纬度，``engine_natal_fn(dt_utc, lat, lon)`` 取西方星盘。
    natal 结果**直接作为象征旁注**，不经过 Provider（Provider 无 western 域）。
  - 源 B（占星仓规则推演）：把源 A 的八字干支映射为 Provider ``BaziInput``
    契约，调 ``provider_fn("bazi", payload)`` 取规则推演 conclusions。

降级矩阵（enrich 永不抛异常）：
  * 缺 birth_datetime        -> degraded，空侧注
  * 引擎不可用（抛异常/available=False）-> degraded（无八字可推）
  * Provider 不可用但 natal 成功 -> natal 仍作侧注（部分降级，不抑制渲染）
  * 两源都抛异常             -> degraded 返回，绝不外溢
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

    依赖注入而非直接持有 DivinationClient：
      1. 本模块不 import 跨仓包（``divination_consumer`` 与 ``engine/`` 同级，
         需由调用方在已配置 sys.path 的上下文里构造并注入）；
      2. 便于测试：注入可控 provider / engine 函数，无需真实 HTTP / 历法。

    P5a 注入点：
      * ``provider_fn(domain, payload) -> Mapping`` —— 源 B（Provider 规则推演）。
      * ``engine_bazi_fn(dt_local, sex) -> Mapping`` —— 源 A（引擎干支历法）。
      * ``engine_natal_fn(dt_utc, lat, lon) -> Mapping`` —— 源 A（西方星盘象征旁注）。
    """

    def __init__(
        self,
        provider_fn: Callable[..., Mapping[str, Any]] | None = None,
        *,
        domain: str = "liuyao",
        security_sink: Callable[[str, str], None] | None = None,
        engine_bazi_fn: Callable[..., Mapping[str, Any]] | None = None,
        engine_natal_fn: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self._provider_fn = provider_fn
        self._domain = domain
        self._security_sink = security_sink
        self._engine_bazi_fn = engine_bazi_fn
        self._engine_natal_fn = engine_natal_fn

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
        """从 Provider 响应中提取象征侧注（legacy ``symbols`` 字段格式）。

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

    @staticmethod
    def _extract_provider_conclusions(raw: Mapping[str, Any], domain: str) -> tuple[SymbolicAnnotation, ...]:
        """把 Provider analyze 响应（``conclusions: [str]``）转为象征侧注。

        Provider ``/api/{domain}/analyze`` 返回
        ``{domain, matched_rules, conclusions, total_weight, matched_count}``，
        其中 ``conclusions`` 是规则推演结论列表。这里把每条结论作为一条象征侧注；
        若响应带 legacy ``symbols`` 字段则回退到 :meth:`_extract_symbols`。
        """
        out: list[SymbolicAnnotation] = []
        for c in raw.get("conclusions", []) or []:
            content = str(c).strip()
            if not content:
                continue
            out.append(
                SymbolicAnnotation(
                    domain=domain,
                    engine="provider-rules",
                    content=content,
                    confidence=SYMBOLIC_CONFIDENCE_MIN,
                    provenance=str(raw.get("domain", "")),
                )
            )
        if out:
            return tuple(out)
        # 回退：legacy symbols 字段
        return DivinationEnrichment._extract_symbols(raw)

    # ------------------------------------------------------------------ 主入口
    def enrich(self, text: str, user: Any | None = None) -> DivinationResult:
        """取回象征侧注。**任何失败都不抛异常、不影响安全流程。**"""
        if user is None:
            # 旧单源路径（P4 兼容）：仅 provider_fn(domain)
            return self._enrich_legacy(text)
        return self._enrich_dual_source(text, user)

    # ------------------------------------------------------------------ 旧单源路径
    def _enrich_legacy(self, text: str) -> DivinationResult:
        """P4 既有行为：``provider_fn(domain)`` -> symbols。"""
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

    # ------------------------------------------------------------------ P5a 双源路径
    def _enrich_dual_source(self, text: str, user: Any) -> DivinationResult:
        """双源：引擎历法（bazi + natal）+ Provider 规则推演。永不抛异常。"""
        birth_dt = getattr(user, "birth_datetime", None) or None
        if not birth_dt:
            return DivinationResult(
                domain="bazi", degraded=True,
                reason="缺出生信息（birth_datetime），双源降级",
            )

        sex = getattr(user, "sex", None) or "男"
        lat = getattr(user, "birth_lat", None)
        lon = getattr(user, "birth_lon", None)

        symbols: list[SymbolicAnnotation] = []
        notes: list[str] = []

        # ---- 源 A1：引擎干支历法（公历→干支走引擎仓 bazi_engine）----
        bazi_data: Mapping[str, Any] | None = None
        if self._engine_bazi_fn is not None:
            try:
                bazi_data = self._engine_bazi_fn(birth_dt, sex)
            except Exception as exc:
                self._emit(
                    SECURITY_EVENT_PROVIDER_UNAVAILABLE,
                    f"engine_bazi 异常 type={type(exc).__name__} detail={exc}",
                )
                bazi_data = None
        if not isinstance(bazi_data, Mapping) or not bazi_data.get("available"):
            bazi_data = None
            notes.append("engine_bazi_unavailable")

        # ---- 源 A2：引擎西方星盘（象征旁注，直接侧注，不经过 Provider）----
        if self._engine_natal_fn is not None and lat is not None and lon is not None:
            try:
                natal_data = self._engine_natal_fn(birth_dt, float(lat), float(lon))
                natal_sym = self._natal_to_annotation(natal_data)
                if natal_sym is not None:
                    symbols.append(natal_sym)
            except Exception as exc:
                self._emit(
                    SECURITY_EVENT_PROVIDER_UNAVAILABLE,
                    f"engine_natal 异常 type={type(exc).__name__} detail={exc}",
                )

        # ---- 源 B：Provider 规则推演（基于源 A 的八字干支）----
        if bazi_data is not None and self._provider_fn is not None:
            payload = self._bazi_to_provider_payload(bazi_data)
            try:
                raw = self._provider_fn("bazi", payload)
                if isinstance(raw, Mapping):
                    symbols.extend(self._extract_provider_conclusions(raw, domain="bazi"))
                else:
                    self._emit(
                        SECURITY_EVENT_MALFORMED,
                        f"provider bazi 响应类型非法: {type(raw).__name__}",
                    )
                    notes.append("provider_malformed")
            except Exception as exc:
                # Provider 不可用：不抑制已取得的 natal 侧注（部分降级）。
                event, code = self._classify(exc)
                self._emit(
                    event,
                    f"domain=bazi type={type(exc).__name__} detail={exc}",
                )
                notes.append(f"provider_{code}")
        elif self._provider_fn is None:
            notes.append("provider_not_configured")

        if not symbols:
            return DivinationResult(
                domain="bazi", degraded=True,
                reason="; ".join(notes) or "无有效象征侧注",
            )

        return DivinationResult(
            domain="bazi",
            symbols=tuple(symbols),
            provenance="engine_lunar+provider_rules",
        )

    @staticmethod
    def _bazi_to_provider_payload(bazi_data: Mapping[str, Any]) -> dict[str, Any]:
        """把引擎 bazi 四柱映射为 Provider ``BaziInput`` 契约。"""
        pillars = bazi_data.get("pillars") or {}

        def _p(name: str) -> tuple[str, str]:
            v = pillars.get(name) or {}
            if not isinstance(v, Mapping):
                return "", ""
            return str(v.get("gan", "") or ""), str(v.get("zhi", "") or "")

        yg, yz = _p("year")
        mg, mz = _p("month")
        dg, dz = _p("day")
        hg, hz = _p("hour")
        return {
            "year_gan": yg,
            "year_zhi": yz,
            "month_gan": mg,
            "month_zhi": mz,
            "day_gan": dg,
            "day_zhi": dz,
            "hour_gan": hg,
            "hour_zhi": hz,
            "ri_zhu_wx": str(bazi_data.get("day_master_wx", "") or ""),
            "ri_zhu_wangshuai": str(bazi_data.get("strength", "平") or "平"),
        }

    @staticmethod
    def _natal_to_annotation(natal_data: Any) -> SymbolicAnnotation | None:
        """把引擎 natal 结果转为一条西方星盘象征旁注。

        该旁注**不经过 Provider**（Provider DOMAINS 仅 liuyao/ziwei/bazi）。
        confidence 由 SymbolicAnnotation 构造时夹到 [0.36, 0.40] 硬锁区间。
        """
        if not isinstance(natal_data, Mapping):
            return None
        if not natal_data.get("available"):
            return None
        bodies = natal_data.get("bodies") or []
        body_str = "、".join(
            f"{b.get('name', '')}{b.get('sign', '')}"
            for b in bodies
            if isinstance(b, Mapping)
        )
        disclaimer = str(natal_data.get("disclaimer", "") or "")
        content = f"西方本命盘（象征旁注）：{body_str}。{disclaimer}".strip()
        return SymbolicAnnotation(
            domain="western",
            engine="xinjing-natal",
            content=content,
            confidence=natal_data.get("confidence", SYMBOLIC_CONFIDENCE_MIN),
            provenance="xinjing-relationship-engine/natal",
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
