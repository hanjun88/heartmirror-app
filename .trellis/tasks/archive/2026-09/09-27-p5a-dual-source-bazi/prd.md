# P5a: DivinationEnrichment 双源链路连通性

## 目标
把 `DivinationEnrichment` 从单源（仅 Provider）扩为双源：
- 源 A（引擎仓历法）：`engine_client.bazi` 干支八字 + `engine_client.natal` 西方星盘（象征旁注，不经过 Provider）。
- 源 B（占星仓规则推演）：把源 A 的八字干支映射为 Provider `BaziInput`，调 `divination_consumer.analyze("bazi", payload)`。

## 接口
- `enrich(text, user=None)`：user 携带 `birth_datetime/birth_lat/birth_lon/sex`。
- 依赖注入：`provider_fn(domain, payload)`、`engine_bazi_fn(dt_local, sex)`、`engine_natal_fn(dt_utc, lat, lon)`。
- user=None 走旧单源路径（`provider_fn(domain)`），保持 P4/ADR-DIV 既有测试绿色。

## 降级矩阵（enrich 永不抛异常）
- 缺 birth_datetime → degraded，空侧注。
- 引擎抛异常 → degraded（无侧注）。
- Provider 抛异常但 natal 成功 → natal 仍作侧注（部分降级，不抑制侧注渲染）。
- 两源都抛 → 返回 degraded，绝不外溢。

## 硬约束
- 不动 P1-P4 安全逻辑（crisis / SAFE_DEGRADED / Meta-Arbiter）。
- Western natal 只作引擎侧象征旁注，不经过 Provider。
- 公历→干支必须走引擎仓 bazi_engine，心镜不自己算。
- render_annotation() 保持当前格式（P5b 才做双轨）。

## 验收
- 7 个 P5a 单测全过；全量 `pytest tests/` 无回归。
- CI 新增三仓真实链路 E2E job（禁 skip，真实 HTTP）。
