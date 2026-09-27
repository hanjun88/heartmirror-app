# -*- coding: utf-8 -*-
"""P5a：心镜 × 引擎仓历法 × Provider 规则推演 —— 真实三仓链路 E2E。

本文件**只**在专用 CI job（``pytest tests/test_p5a_e2e.py -m e2e``）中运行。禁 skip：
任何组件（引擎仓 import / Provider uvicorn 子进程）启动失败直接 fail。

注意：模块顶层**不**做任何 env 校验 / sys.path 改写，确保普通 CI job 用
``-m "not e2e"`` 收集本文件时不会因环境缺失而在 collection 阶段报错；
所有重活都在被 ``e2e`` marker 选中后于 fixture / 测试函数内执行。

链路（全部真实，无 MockTransport / 无 mock 引擎）：
    心镜 DivinationEnrichment.enrich(user=带出生信息)
      -> engine_client.bazi(dt_local, sex)   [真实 lunar-python 排干支]
      -> engine_client.natal(dt_utc, lat, lon) [真实 pyswisseph 西方星盘]
      -> provider_fn("bazi", BaziInput 干支)   [divination_consumer -> 真实 HTTP -> Provider uvicorn]
      -> 合并 western 象征旁注 + Provider conclusions 侧注

环境变量（CI job 注入）：
  XINJING_ENGINE_PATH           引擎仓根目录（含 engine/ 与 divination_consumer/）
  DIVINATION_PROVIDER_ENGINE_DIR  Provider repo 的 engine/ 目录
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.e2e

PROVIDER_HOST = "127.0.0.1"
PROVIDER_PORT = 8791


def _setup_engine_path() -> Path:
    """校验并注入引擎仓到 sys.path；失败直接 raise（不 skip）。

    必须在 import backend.engine_client 之前调用——engine_client 在 import 时读取
    XINJING_ENGINE_PATH 并 sys.path 注入引擎仓。
    """
    engine_path = os.environ.get("XINJING_ENGINE_PATH", "").strip()
    if not engine_path:
        raise RuntimeError(
            "E2E 要求环境变量 XINJING_ENGINE_PATH 指向引擎仓根目录（含 engine/ 与 "
            "divination_consumer/）；禁止 skip，直接 fail。"
        )
    engine_root = Path(engine_path)
    if not (engine_root / "engine" / "bazi_engine.py").is_file():
        raise RuntimeError(
            f"XINJING_ENGINE_PATH={engine_path} 不是有效引擎仓根目录"
            f"（缺少 engine/bazi_engine.py）；禁止 skip，直接 fail。"
        )
    if str(engine_root) not in sys.path:
        sys.path.insert(0, str(engine_root))
    return engine_root


@pytest.fixture(scope="session")
def provider_server():
    """session 级 fixture：启动真实 Provider uvicorn 子进程；失败直接 raise（不 skip）。"""
    engine_dir = os.environ.get("DIVINATION_PROVIDER_ENGINE_DIR", "").strip()
    if not engine_dir:
        raise RuntimeError(
            "E2E 要求环境变量 DIVINATION_PROVIDER_ENGINE_DIR 指向 Provider repo 的 "
            "engine/ 目录；禁止 skip，直接 fail。"
        )
    engine_dir = Path(engine_dir)
    app_py = engine_dir / "main.py"
    if not app_py.is_file():
        raise RuntimeError(f"Provider main.py 不存在: {app_py}；禁止 skip，直接 fail。")

    # 让 divination_consumer 的 DivinationClient 默认指向本 fixture 启动的 Provider。
    os.environ["DIVINATION_PROVIDER_URL"] = f"http://{PROVIDER_HOST}:{PROVIDER_PORT}"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", PROVIDER_HOST, "--port", str(PROVIDER_PORT)],
        cwd=str(engine_dir),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    import httpx  # 真实 TCP，无 transport 拦截

    ready = False
    deadline = time.time() + 30.0
    try:
        while time.time() < deadline:
            ret = proc.poll()
            if ret is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
                raise RuntimeError(
                    f"Provider 子进程提前退出 rc={ret} (cwd={engine_dir})\n---- output ----\n{out}"
                )
            try:
                r = httpx.get(f"http://{PROVIDER_HOST}:{PROVIDER_PORT}/health", timeout=1.0)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        if not ready:
            raise RuntimeError(
                f"Provider 在 30s 内未就绪（http://{PROVIDER_HOST}:{PROVIDER_PORT}/health）；"
                "禁止 skip，直接 fail。"
            )
        yield f"http://{PROVIDER_HOST}:{PROVIDER_PORT}"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def test_p5a_real_three_repo_chain(provider_server):
    """真实三仓链路：心镜 -> 引擎历法(bazi/natal) -> divination_consumer -> Provider HTTP。"""
    # 1. 先注入引擎仓路径（必须在 import backend.engine_client 之前）。
    _setup_engine_path()

    # 2. 延迟 import：此时 XINJING_ENGINE_PATH 已就位，engine_client 加载真实引擎。
    from backend import engine_client
    from backend.divination.enrichment import DivinationEnrichment
    from backend.divination.provider_factory import build_provider_fn

    assert engine_client.is_engine_available(), (
        "E2E 要求引擎仓真实加载成功（is_engine_available=True）；"
        "CI 需 pip install lunar_python pyswisseph。"
    )

    # 3. provider_fn 必须真的接到 divination_consumer（非 None）
    provider_fn = build_provider_fn()
    assert provider_fn is not None, (
        "build_provider_fn() 应成功导入 divination_consumer 并返回 callable"
    )

    # 4. 构造双源 enrichment：引擎历法 + Provider 规则推演
    enr = DivinationEnrichment(
        provider_fn=provider_fn,
        engine_bazi_fn=engine_client.bazi,
        engine_natal_fn=engine_client.natal,
    )

    # 5. 带出生信息的 user（公历时间 + 经纬度）
    user = SimpleNamespace(
        birth_datetime="1990-05-20T14:30:00",
        sex="男",
        birth_lat=39.9,
        birth_lon=116.4,
    )

    # 6. 真实 enrich：任何内部故障都不得外溢（enrich 永不抛异常）
    result = enr.enrich("今天和伴侣聊得不错，关系在缓和", user=user)

    # 7. 经纬度给了 -> western natal 象征旁注一定交付（引擎真实排盘）
    western = [s for s in result.symbols if s.domain == "western"]
    assert western, f"应交付 western natal 象征旁注，实际 symbols={result.symbols!r}"
    natal_sym = western[0]
    assert natal_sym.engine == "xinjing-natal"
    # 象征置信硬锁区间 [0.36, 0.40]
    assert 0.36 <= natal_sym.confidence <= 0.40
    assert "西方本命盘" in natal_sym.content

    # 8. Provider 真实 HTTP 已打通：直接发一发 bazi/analyze 真实 TCP（无 MockTransport）。
    import httpx
    resp = httpx.post(
        f"{provider_server}/api/bazi/analyze",
        json={
            "year_gan": "甲", "year_zhi": "子",
            "month_gan": "癸", "month_zhi": "巳",
            "day_gan": "丙", "day_zhi": "寅",
            "hour_gan": "甲", "hour_zhi": "午",
        },
        timeout=10.0,
    )
    assert resp.status_code == 200, (
        f"Provider bazi/analyze 真实 HTTP 失败: {resp.status_code} {resp.text[:300]}"
    )
    body = resp.json()
    assert body["domain"] == "bazi"
    assert "conclusions" in body and "matched_rules" in body

    # 8b. 契约实质性断言（防"绿灯放过断裂"）：
    #     仅断言 HTTP 200 / 字段存在，会让"Provider 扩了 847 条而 Consumer
    #     vendor 仍停在 206 条"这类断裂被 200 掩盖。必须校验：
    #       (a) list_rules 经 Consumer 门禁（count + content_sha256 双重 fail-closed）
    #       (b) 返回条数与 Provider 真实下发条数一致
    from divination_consumer import DivinationClient  # noqa: E402

    with DivinationClient(base_url=provider_server) as consumer:
        for domain in ("bazi", "liuyao", "ziwei", "vedic", "western"):
            rules = consumer.list_rules(domain)   # 门禁不通过会 raise
            assert rules["count"] == len(rules["rules"]), (
                f"{domain}: count={rules['count']} != len(rules)={len(rules['rules'])}"
            )
            assert isinstance(rules.get("content_sha256"), str) and rules["content_sha256"], (
                f"{domain}: 缺少 content_sha256 内容指纹"
            )
        # 与 Provider 裸 HTTP 对齐，排除 Consumer 自身缓存导致的假一致
        raw = httpx.get(f"{provider_server}/api/rules/bazi", timeout=10.0).json()
        gated = DivinationClient(base_url=provider_server).list_rules("bazi")
        assert gated["count"] == raw["count"], (
            f"Consumer 门禁放行的条数 {gated['count']} 与 Provider 实际 {raw['count']} 不一致"
        )
        assert gated["content_sha256"] == raw["content_sha256"], (
            "Consumer 校验的内容指纹与 Provider 实际下发不一致"
        )

    # 9. enrich 整体不抛、且至少有 western 旁注交付（部分降级也算连通）。
    assert result.is_usable or result.symbols, (
        f"双源链路应交付至少一条侧注，实际 degraded={result.degraded} reason={result.reason!r}"
    )
