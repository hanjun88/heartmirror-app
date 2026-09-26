# 心镜应用 CI 修复

## 问题
本地 63 测试全过，GitHub Actions pytest 失败。

## 修复
1. 干净 venv 模拟 CI: 只装 backend/requirements.txt，设 JWT_SECRET，跑 pytest
2. 定位具体失败测试和错误
3. 修复代码或 CI 配置
4. 确保 63 测试在 CI 环境全过

## 验收
- 干净环境 pytest 全过
- CI workflow 配置正确
- commit + push main
