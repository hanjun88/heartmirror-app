# -*- coding: utf-8 -*-
"""pytest 全局夹具：在导入 backend 之前设置测试环境变量。

P1-C 安全加固后，backend.config 在 JWT_SECRET 缺失或长度 <32 时会 raise，
因此测试必须在此处先行注入合法的测试密钥与数据库配置。
"""
import os

# 测试用 JWT 密钥（>=32 字符），绝不用于生产
os.environ.setdefault(
    "JWT_SECRET",
    "heartmirror-test-jwt-secret-key-at-least-32-chars-long",
)
# 测试数据库由 tests/test_api.py 用内存 SQLite override，这里给个兜底
os.environ.setdefault("DATABASE_URL", "sqlite:///./heartmirror_test.db")
# 测试环境不真实调用 LLM / 外部引擎
os.environ.setdefault("WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
