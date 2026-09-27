# -*- coding: utf-8 -*-
"""P5：把 DivinationEnrichment.provider_fn 接到真实 divination_consumer 客户端。

设计目标
========
``build_provider_fn()`` 返回一个符合 ``(domain, text) -> Mapping`` 签名的 provider_fn，
其内部通过 ``sys.path`` 按需导入引擎仓的 ``divination_consumer.client.DivinationClient``，
调用 ``client.analyze(domain, {"text": text})`` 访问 Provider（divination-knowledge-engine）。

安全降级（fail-open for enrichment）
====================================
enrichment 永远是可选侧注，不是 safety dependency。以下任一情况都必须返回 ``None``
（由 ``DivinationEnrichment.enrich()`` 自动走 degraded 路径，对话不阻断）：

  * 环境变量 ``XINJING_ENGINE_PATH`` 未设置；
  * 指向的路径不存在；
  * ``import divination_consumer`` 失败（引擎仓未 checkout / 依赖缺失）。

注意
====
* **不** pip install 整个引擎包——只用 ``sys.path`` 按需导入。
* 模块 import 时**不**执行任何网络调用，也**不**构造 DivinationClient（懒加载）：
  客户端在 provider_fn 首次被调用时才构造（构造仅读取本地 vendor manifest/schema，
  不发网络包），并缓存在闭包内。
* provider_fn 自身**不**捕获异常——任何 ``analyze`` 异常（连接失败 / 超时 / 非 2xx /
  manifest 校验失败）都直接抛出，由 ``DivinationEnrichment.enrich()`` 的 try/except
  兜底为 degraded。这是 ADR-DIV-001 的故障隔离边界。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("heartmirror.divination.provider_factory")

#: 环境变量名：引擎仓根目录（含 divination_consumer/ 包）。
ENV_ENGINE_PATH = "XINJING_ENGINE_PATH"


def build_provider_fn() -> Callable[[str, str], Mapping[str, Any]] | None:
    """构造一个调用真实 divination_consumer 的 provider_fn；失败返回 None。

    返回的 callable 签名为 ``(domain: str, text: str) -> Mapping[str, Any]``：
      * 首次调用时懒加载构造 ``DivinationClient``（默认 base_url 来自环境变量
        ``DIVINATION_PROVIDER_URL``，默认 http://localhost:8000；manifest/schema
        使用引擎仓 vendor 内的默认契约快照）；
      * 调用 ``client.analyze(domain, {"text": text})`` 并返回原始响应 dict；
      * 任何异常直接抛出，由 ``DivinationEnrichment.enrich()`` 捕获并 degraded。

    Returns:
        可调用的 provider_fn；当引擎路径不可用或 import 失败时返回 ``None``。
    """
    engine_path = os.environ.get(ENV_ENGINE_PATH)
    if not engine_path:
        logger.warning(
            "[%s] 未设置，占星 provider 不可用（enrichment 自动 degraded）",
            ENV_ENGINE_PATH,
        )
        return None

    engine_dir = Path(engine_path)
    if not engine_dir.is_dir():
        logger.warning(
            "[%s] 路径不存在或非目录: %s，占星 provider 不可用（degraded）",
            ENV_ENGINE_PATH,
            engine_path,
        )
        return None

    try:
        engine_str = str(engine_dir)
        if engine_str not in sys.path:
            sys.path.insert(0, engine_str)
        from divination_consumer.client import DivinationClient  # type: ignore
    except Exception:
        logger.warning(
            "import divination_consumer.client 失败（XINJING_ENGINE_PATH=%s），"
            "占星 provider 不可用（degraded）",
            engine_path,
            exc_info=True,
        )
        return None

    # 闭包内懒加载并缓存客户端：模块 import 时不构造、不联网。
    client_holder: dict[str, Any] = {}

    def provider_fn(domain: str, text: str) -> Mapping[str, Any]:
        if "client" not in client_holder:
            # DivinationClient() 构造仅读取本地 vendor manifest/schema，不发网络包。
            client_holder["client"] = DivinationClient()
        # payload 字段名：以 Provider 实际 analyze 端点为准。当前 Provider 的
        # /api/{domain}/analyze 期望结构化占卜事实（六爻需 month_zh/day_zh/yong_shen），
        # 自由文本会被 FastAPI 判 422 -> DivinationAPIError -> enrich() degraded。
        # P5 仅打通 transport，不做 chat-text -> 占卜事实的抽取；这是预期的安全降级。
        return client_holder["client"].analyze(domain, {"text": text})

    return provider_fn
