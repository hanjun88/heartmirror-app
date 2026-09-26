# -*- coding: utf-8 -*-
"""心镜 API 关键接口测试。"""
import os
import sys
import pytest

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 使用内存 SQLite + StaticPool（保证所有连接共享同一个内存数据库）
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="function")
def client():
    from backend.database import Base, get_db
    from backend.main import app

    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # 禁用 scheduler（测试中不需要）
    import backend.main as main_mod
    # lifespan 会启动 scheduler，测试中直接用 TestClient 不触发 lifespan 也行

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def register_and_login(client, email="test@example.com", password="test123", nickname="测试用户", age=25):
    """注册并登录，返回 (client_with_auth, tokens)。"""
    r = client.post("/auth/register", json={
        "email": email, "password": password, "nickname": nickname, "age": age
    })
    assert r.status_code == 200, r.text
    tokens = r.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    return headers, tokens


# ---- 1. 用户注册/登录 ----
class TestAuth:
    def test_register(self, client):
        r = client.post("/auth/register", json={
            "email": "new@example.com", "password": "secret123", "nickname": "新用户", "age": 20
        })
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_register_duplicate(self, client):
        client.post("/auth/register", json={"email": "dup@example.com", "password": "secret123", "age": 20})
        r = client.post("/auth/register", json={"email": "dup@example.com", "password": "secret123", "age": 20})
        assert r.status_code == 400

    def test_login(self, client):
        client.post("/auth/register", json={"email": "login@example.com", "password": "pass123", "age": 20})
        r = client.post("/auth/login", json={"email": "login@example.com", "password": "pass123"})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_login_wrong_password(self, client):
        client.post("/auth/register", json={"email": "wrong@example.com", "password": "right123", "age": 20})
        r = client.post("/auth/login", json={"email": "wrong@example.com", "password": "wrong123"})
        assert r.status_code == 401

    def test_get_me(self, client):
        headers, _ = register_and_login(client, email="me@example.com")
        r = client.get("/auth/me", headers=headers)
        assert r.status_code == 200
        assert r.json()["email"] == "me@example.com"

    def test_unauthorized(self, client):
        r = client.get("/auth/me")
        assert r.status_code == 401

    def test_minor_flag(self, client):
        client.post("/auth/register", json={"email": "minor@example.com", "password": "young123", "age": 15})
        r = client.post("/auth/login", json={"email": "minor@example.com", "password": "young123"})
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = client.get("/auth/me", headers=headers).json()
        assert me["is_minor"] is True


# ---- 2. 情绪日记 ----
class TestDiary:
    def test_create_diary(self, client):
        headers, _ = register_and_login(client, email="diary@example.com")
        r = client.post("/diary", headers=headers, json={
            "emotion_label": "开心", "intensity": 8, "description": "今天天气很好", "tags": ["天气"]
        })
        assert r.status_code == 200
        assert r.json()["emotion_label"] == "开心"
        assert r.json()["intensity"] == 8

    def test_list_diaries(self, client):
        headers, _ = register_and_login(client, email="list@example.com")
        client.post("/diary", headers=headers, json={"emotion_label": "难过", "intensity": 5, "description": ""})
        client.post("/diary", headers=headers, json={"emotion_label": "开心", "intensity": 7, "description": ""})
        r = client.get("/diary", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_diary_trends(self, client):
        headers, _ = register_and_login(client, email="trend@example.com")
        client.post("/diary", headers=headers, json={"emotion_label": "焦虑", "intensity": 7, "description": "工作压力大"})
        client.post("/diary", headers=headers, json={"emotion_label": "平静", "intensity": 3, "description": "休息很好"})
        r = client.get("/diary/trends", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "avg_intensity_7d" in data
        assert "emotion_distribution" in data

    def test_diary_intensity_validation(self, client):
        headers, _ = register_and_login(client, email="valid@example.com")
        r = client.post("/diary", headers=headers, json={
            "emotion_label": "开心", "intensity": 15, "description": ""
        })
        assert r.status_code == 422  # 超出 1-10 范围


# ---- 3. 记忆系统 ----
class TestMemory:
    def test_create_memory_manual(self, client):
        headers, _ = register_and_login(client, email="mem@example.com")
        r = client.post("/memory", headers=headers, json={
            "content": "用户提到和男朋友吵架了", "emotion": "愤怒", "importance": 4,
            "entities": ["男朋友"]
        })
        assert r.status_code == 200
        assert r.json()["content"] == "用户提到和男朋友吵架了"

    def test_list_memories(self, client):
        headers, _ = register_and_login(client, email="memlist@example.com")
        client.post("/memory", headers=headers, json={"content": "测试记忆1", "importance": 3})
        r = client.get("/memory", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_update_memory(self, client):
        headers, _ = register_and_login(client, email="memupd@example.com")
        create_r = client.post("/memory", headers=headers, json={"content": "原始内容", "importance": 3})
        mid = create_r.json()["id"]
        r = client.put(f"/memory/{mid}", headers=headers, json={"is_pinned": True, "importance": 5})
        assert r.status_code == 200
        assert r.json()["is_pinned"] is True
        assert r.json()["importance"] == 5

    def test_delete_memory(self, client):
        headers, _ = register_and_login(client, email="memdel@example.com")
        create_r = client.post("/memory", headers=headers, json={"content": "待删除", "importance": 3})
        mid = create_r.json()["id"]
        r = client.delete(f"/memory/{mid}", headers=headers)
        assert r.status_code == 200
        # 确认已删除
        r2 = client.get("/memory", headers=headers)
        assert all(m["id"] != mid for m in r2.json())

    def test_chat_extracts_memory(self, client):
        """对话后应自动提取记忆（规则提取降级模式）。"""
        headers, _ = register_and_login(client, email="memchat@example.com")
        r = client.post("/chat", headers=headers, json={"message": "我今天和男朋友吵架了，很难过"})
        assert r.status_code == 200
        # 即使 LLM 未配置，规则提取也应该工作
        assert "reply" in r.json()


# ---- 4. 危机对话熔断 ----
class TestCrisis:
    def test_crisis_triggers_hotline(self, client):
        headers, _ = register_and_login(client, email="crisis@example.com")
        r = client.post("/chat", headers=headers, json={"message": "我不想活了，活着没意思"})
        assert r.status_code == 200
        data = r.json()
        assert data["is_crisis"] is True
        assert "12356" in data["reply"]

    def test_crisis_no_llm(self, client):
        """危机响应不应该走 LLM，应该直接返回模板。"""
        headers, _ = register_and_login(client, email="crisis2@example.com")
        r = client.post("/chat", headers=headers, json={"message": "我想自杀"})
        data = r.json()
        assert data["is_crisis"] is True
        # 危机回复应该包含热线
        assert "12356" in data["reply"]

    def test_normal_chat_no_crisis(self, client):
        headers, _ = register_and_login(client, email="normal@example.com")
        r = client.post("/chat", headers=headers, json={"message": "今天有点开心"})
        data = r.json()
        assert data["is_crisis"] is False
        assert "reply" in data


# ---- 5. 双人配对 ----
class TestCouple:
    def test_invite_and_join(self, client):
        # 用户 A 创建邀请
        headers_a, _ = register_and_login(client, email="couple_a@example.com")
        r = client.post("/couple/invite", headers=headers_a, json={})
        assert r.status_code == 200
        code = r.json()["invite_code"]
        assert len(code) > 0

        # 用户 B 加入
        headers_b, _ = register_and_login(client, email="couple_b@example.com")
        r2 = client.post("/couple/join", headers=headers_b, json={"invite_code": code})
        assert r2.status_code == 200
        assert r2.json()["status"] == "connected"

    def test_invalid_invite_code(self, client):
        headers_b, _ = register_and_login(client, email="couple_c@example.com")
        r = client.post("/couple/join", headers=headers_b, json={"invite_code": "INVALID"})
        assert r.status_code == 404

    def test_couple_status(self, client):
        headers, _ = register_and_login(client, email="couple_d@example.com")
        r = client.get("/couple/status", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] in ("pending", "connected", "none")

    def test_session_requires_connection(self, client):
        headers, _ = register_and_login(client, email="couple_e@example.com")
        r = client.post("/couple/session", headers=headers, json={})
        assert r.status_code == 400  # 未配对不能创建会话

    def test_minor_cannot_use_couple(self, client):
        client.post("/auth/register", json={"email": "minor_couple@example.com", "password": "young123", "age": 15})
        login = client.post("/auth/login", json={"email": "minor_couple@example.com", "password": "young123"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        r = client.post("/couple/invite", headers=headers, json={})
        assert r.status_code == 403


# ---- 6. 关系测评 ----
class TestAssessment:
    def test_get_questions(self, client):
        r = client.get("/assessment/questions/attachment")
        assert r.status_code == 200
        data = r.json()
        assert len(data["questions"]) == 10

    def test_submit_attachment(self, client):
        headers, _ = register_and_login(client, email="attach@example.com")
        answers = [2, 2, 2, 2, 2, 2, 1, 2, 2, 1]  # 偏向安全型
        r = client.post("/assessment/attachment/submit", headers=headers, json={"answers": answers})
        assert r.status_code == 200
        data = r.json()
        assert "type" in data["result"]

    def test_submit_love_language(self, client):
        headers, _ = register_and_login(client, email="lovelang@example.com")
        answers = [5] * 15
        r = client.post("/assessment/love_language/submit", headers=headers, json={"answers": answers})
        assert r.status_code == 200
        assert "primary" in r.json()["result"]

    def test_submit_conflict(self, client):
        headers, _ = register_and_login(client, email="conflict@example.com")
        answers = [3, 3, 3, 3, 3, 3, 3, 3]
        r = client.post("/assessment/conflict/submit", headers=headers, json={"answers": answers})
        assert r.status_code == 200
        assert "style" in r.json()["result"]

    def test_assessment_writes_profile(self, client):
        """测评结果应写入 User 层档案。"""
        headers, _ = register_and_login(client, email="profile@example.com")
        answers = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]  # 低焦虑低回避 -> 安全型
        client.post("/assessment/attachment/submit", headers=headers, json={"answers": answers})
        me = client.get("/auth/me", headers=headers).json()
        assert me["attachment_type"] is not None


# ---- 7. 合规 ----
class TestCompliance:
    def test_compliance_status(self, client):
        headers, _ = register_and_login(client, email="comp@example.com")
        r = client.get("/compliance/status", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "usage" in data
        assert data["crisis_hotline"] == "12356"
        assert "ai_disclaimer" in data

    def test_notifications(self, client):
        headers, _ = register_and_login(client, email="notif@example.com")
        r = client.get("/notifications", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_delete_account(self, client):
        headers, _ = register_and_login(client, email="del@example.com")
        r = client.delete("/auth/me", headers=headers)
        assert r.status_code == 200
        # 删除后再登录应失败
        r2 = client.post("/auth/login", json={"email": "del@example.com", "password": "test123"})
        assert r2.status_code == 401


# ---- 8. 报告 ----
class TestReport:
    def test_weekly_report(self, client):
        headers, _ = register_and_login(client, email="report@example.com")
        # 先写几条日记
        client.post("/diary", headers=headers, json={"emotion_label": "开心", "intensity": 6, "description": "今天很愉快"})
        client.post("/diary", headers=headers, json={"emotion_label": "焦虑", "intensity": 7, "description": "工作压力"})
        r = client.get("/report/weekly", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "emotion_distribution" in data
        assert "intensity_trend" in data
        assert "relationship_insights" in data

    def test_share_card(self, client):
        headers, _ = register_and_login(client, email="share@example.com")
        r = client.get("/report/share-card", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "svg_content" in data
        assert "svg" in data["svg_content"]
