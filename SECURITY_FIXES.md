# Security Fixes Attribution

本文件记录各安全修复对应的真实 commit，避免因 commit message 误导复审定位。

| 修复项 | 真实 Commit | 说明 |
|--------|------------|------|
| P0-B fail-closed | e1e3ff8 | 特性提交中包含业务安全代码 |
| P1-C JWT 强化 | e1e3ff8 | JWT ≥32 强制 + 固定 HS256 + require exp |
| P1-D 双人配对 BOLA | e1e3ff8 | _assert_participant 覆盖三处调用 |
| P1-E CORS allowlist | e1e3ff8 | 显式 CORS 白名单 |
| F1 双重中介 | e1e3ff8 | 双仲裁路径 |
| F2 单一安全权威 | e1e3ff8 | 统一安全仲裁 |
| CI 配置 | 441bb08 | 仅新增 ci.yml，无业务代码 |

注意：441bb08 的 commit message 声称包含上述安全修复，实际仅为 CI 配置。安全代码均在 e1e3ff8。
