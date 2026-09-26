# R2: CI 接入真实引擎 + arbitrate 契约测试

## Goal

CI checkout 真实 xinjing-relationship-engine(pin SHA 4c4d8da), 运行 arbitrate() 真实契约测试, 保留 SAFE_DEGRADED 降级路径覆盖。

## Requirements

1. CI workflow 新增 step: checkout private 引擎仓到 `./xinjing-engine`, ref pin = `4c4d8da3e5463bc0016c9e267227e6875854f45d`
2. CI 安装引擎额外依赖: `pyswisseph`, `lunar_python`
3. CI 设置 `XINJING_ENGINE_PATH=${{ github.workspace }}/xinjing-engine`
4. 保留独立 step 用 `/nonexistent-engine-path` 跑原有测试, 确保 SAFE_DEGRADED 降级路径仍有覆盖
5. 新建 `tests/test_engine_contract.py`, `@pytest.mark.engine_contract`, 真实调用引擎禁止 mock:
   - (a) `is_engine_available()` 在真实路径下返回 True
   - (b) 正常输入返回字段完整性: is_crisis/matched_terms/target_level/intervention/hotline/is_symbolic_annotation 全存在
   - (c) 正常输入 target_level != SAFE_DEGRADED
   - (d) 危机关键词 "我想自杀" → is_crisis=True 且 target_level=CRISIS
   - (e) target_level 取值域 ⊆ {CRISIS, SAFETY_PLAN, STABILIZE, REPAIR, SYMBOLIC, SAFE_DEGRADED}
   - (f) 非危机输入 target_level ∈ {SAFETY_PLAN, STABILIZE, REPAIR, SYMBOLIC} (实际本地验证为 REPAIR)

## Acceptance Criteria

- [ ] CI yml 中引擎 SHA 硬编码 pin, 不用 @main
- [ ] `tests/test_engine_contract.py` 全部通过 (真实引擎路径)
- [ ] 原有 `tests/` 全部通过, 无回归
- [ ] SAFE_DEGRADED 降级路径测试仍保留并通过
- [ ] commit message: `feat(r2): CI 接入真实引擎 + arbitrate 契约测试`
- [ ] push origin main 成功

## Notes

- 不修改引擎仓代码, 只改心镜仓
- 心镜 config.py 要求 JWT_SECRET >=32 字符
- 本地验证: XINJING_ENGINE_PATH 指向本地引擎绝对路径
