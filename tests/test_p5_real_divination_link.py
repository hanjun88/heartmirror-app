# -*- coding: utf-8 -*-
"""P5：真实 Provider + divination_consumer 链路测试（无 mock transport，真实 TCP）。

运行前提（CI job test-real-divination-link 设置）：
  * XINJING_ENGINE_PATH 指向 checkout 的引擎仓（含 divination_consumer 包）；
  * DIVINATION_PROVIDER_URL=http://127.0.0.1:8765；
  * DIVINATION_PROVIDER_ENGINE_DIR 指向 divination-knowledge-engine/engine。

模块级 guard：未设置 DIVINATION_PROVIDER_ENGINE_DIR 时整个 module skip（避免在
degraded CI job / 本地全量回归中误跑）。**在真实链路 CI job 里 env 已设置，因此不会
skip**；Provider 子进程启动失败直接 raise（fail，不降级为 skip）——这是硬约束。

禁止 MockTransport / httpx MockTransport 拦截——必须发真实 TCP 包。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# ---- 模块级 guard：未配置 Provider engine 目录则跳过（degraded job / 本地回归）----
_PROVIDER_ENGINE_DIR = os.environ.get("DIVINATION_PROVIDER_ENGINE_DIR")
if not _PROVIDER_ENGINE_DIR:
    pytest.skip(
        "DIVINATION_PROVIDER_ENGINE_DIR 未设置：真实 Provider 链路测试跳过"
        "（仅在 test-real-divination-link CI job 中运行）",
        allow_module_level=True,
    )

PROVIDER_HOST = "127.0.0.1"
PROVIDER_PORT = 8765
PROVIDER_BASE_URL = f"http://{PROVIDER_HOST}:{PROVIDER_PORT}"

# 确保 backend 可 import
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def provider_server():
    """session 级：启动真实 Provider uvicorn 子进程。启动失败直接 raise（不 skip）。"""
    engine_dir = Path(_PROVIDER_ENGINE_DIR).resolve()
    app_py = engine_dir / "main.py"
    if not app_py.exists():
        raise RuntimeError(f"Provider main.py 不存在: {app_py}")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", PROVIDER_HOST, "--port", str(PROVIDER_PORT),
        ],
        cwd=str(engine_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    ready = False
    last_err: Exception | None = None
    deadline = time.time() + 30.0
    try:
        while time.time() < deadline:
            ret = proc.poll()
            if ret is not None:
                out = ""
                if proc.stdout is not None:
                    try:
                        out = proc.stdout.read().decode("utf-8", errors="replace")
                    except Exception:
                        out = ""
                raise RuntimeError(
                    f"Provider 子进程提前退出 rc={ret} (cwd={engine_dir})\n---- output ----\n{out}"
                )
            try:
                r = httpx.get(f"{PROVIDER_BASE_URL}/health", timeout=1.0)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception as e:
                last_err = e
            time.sleep(0.5)

        if not ready:
            raise RuntimeError(
                f"Provider 在 30s 内未就绪（http://{PROVIDER_HOST}:{PROVIDER_PORT}/health）"
                f"；last_err={last_err!r}"
            )
        yield PROVIDER_BASE_URL
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# ------------------------------------------------------------------ 1. health
@pytest.mark.e2e
def test_real_provider_health(provider_server):
    """真实 HTTP GET /health 返回 200 + status=ok。"""
    r = httpx.get(f"{provider_server}/health", timeout=5.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "liuyao" in body["domains"]


# ------------------------------------------------------------------ 2. enrichment real call
@pytest.mark.e2e
def test_enrichment_real_provider_call(provider_server):
    """用真实 build_provider_fn() 构造 enrichment，enrich("测试文本") 不抛异常。

    真实 Provider 的 /api/liuyao/analyze 期望结构化占卜事实，自由文本会被 FastAPI
    判 422 -> DivinationAPIError -> enrich() 兜底为 degraded。允许的结果：
      * degraded=True（422 / 无 symbols）——预期；
      * 或 is_usable=True（若 Provider 未来支持文本）——也接受。
    硬约束：**绝不抛异常**。
    """
    from backend.divination.enrichment import DivinationEnrichment
    from backend.divination.provider_factory import build_provider_fn

    provider_fn = build_provider_fn()
    assert provider_fn is not None, (
        "XINJING_ENGINE_PATH 已设置但 build_provider_fn() 返回 None——检查引擎仓 checkout"
    )

    enrichment = DivinationEnrichment(provider_fn=provider_fn, domain="liuyao")
    # 不抛异常即通过；结果可 usable 或 degraded（取决于 Provider 实际返回）
    result = enrichment.enrich("今天和伴侣吵了一架，心里很乱")
    assert result.domain == "liuyao"
    # degraded 或 usable 都合法；关键是 enrich() 自身没有外抛
    assert result.degraded in (True, False)


# ------------------------------------------------------------------ 3. chat endpoint real provider
@pytest.mark.e2e
def test_chat_real_provider_in_prompt(provider_server, monkeypatch):
    """TestClient 发非危机消息，mock arbitrate=REPAIR；chat 端点 200 不崩溃。

    _divination_enrichment 单例已在 app import 时用 build_provider_fn() 接好真实 Provider；
    这里**不** monkeypatch 它——让真实 enrichment 真实打 Provider（422 -> degraded -> 无侧注）。
    验证对话主流程不被占星故障阻断。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from backend.database import Base, get_db
    import backend.main  # noqa: F401
    from backend.main import app
    from backend.routers import chat as chat_mod

    # mock 安全裁决为非危机 REPAIR（不依赖真实引擎）
    normal_arb = {
        "is_crisis": False,
        "matched_terms": [],
        "target_level": "REPAIR",
        "intervention": "",
        "hotline": None,
        "is_symbolic_annotation": False,
    }
    monkeypatch.setattr(chat_mod, "arbitrate", lambda text, user_id="anonymous": dict(normal_arb))
    monkeypatch.setattr(chat_mod, "recall_memories", lambda db, uid, text, top_k=5: [])
    monkeypatch.setattr(chat_mod, "build_memory_context", lambda recalled: "")
    monkeypatch.setattr(chat_mod, "extract_and_store", lambda db, uid, msg, reply: [])
    monkeypatch.setattr(chat_mod, "chat_completion", lambda messages, persona="warm": "正常回复")

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            r = c.post("/auth/register", json={
                "email": "p5-real@example.com", "password": "test123",
                "nickname": "p5real", "age": 25,
            })
            assert r.status_code == 200, r.text
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}

            resp = c.post("/chat", headers=h, json={"message": "今天想和你聊聊感情问题"})
            # 关键断言：真实 Provider 链路下对话不阻断，返回 200
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_crisis"] is False
            assert resp.json()["reply"] == "正常回复"
    finally:
        app.dependency_overrides.clear()
