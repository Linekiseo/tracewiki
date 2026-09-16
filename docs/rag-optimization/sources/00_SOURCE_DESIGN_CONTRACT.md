# 单源详细设计统一契约

每个来源的设计必须完整回答以下问题。缺少任一章节时，该源不能进入多源协同设计。

## 1. 来源边界

- 物理数据入口是什么；
- 哪些内容属于该来源；
- 哪些内容只是派生索引；
- 哪些内容属于其他来源；
- 当前支持和未来支持的适配器；
- 稳定实体、版本和 ACL 边界。

## 2. 当前实现审计

- 摄取入口；
- 原始数据保存；
- 领域实体；
- 关系；
- Search View；
- 召回；
- 重排；
- Context；
- 增量更新；
- 评测；
- 当前活跃数据规模；
- 已知失败模式。

## 3. 查询任务

每一种查询任务都要定义：

- 用户问题；
- Query Profile；
- 必需证据角色；
- 候选通道；
- 允许关系；
- 版本/时间规则；
- Context 模板；
- 拒答条件；
- Golden Case。

## 4. 目标数据与索引

- Stable Entity；
- Retrieval Unit；
- Parent/Child；
- exact/sparse/dense/multi-vector；
- graph adjacency；
- structured index；
- model/index/builder version；
- content-addressed cache；
- generation 发布。

## 5. 检索算法

- Query rewrite；
- channel candidate budget；
- source-internal fusion；
- reranker；
- calibration；
- graph/parent expansion；
- duplicate handling；
- adaptive-k；
- timeout/partial semantics。

## 6. Context

- Retrieval Context；
- Comprehension Context；
- token allocation；
- source-specific rendering；
- Citation locator；
- conflicting/missing evidence。

## 7. 正确性与治理

- version；
- temporal validity；
- fact status；
- relation derivation；
- review status；
- ACL；
- sensitive data；
- deletion/tombstone；
- index rebuild；
- fallback/rollback。

## 8. 评测

- Golden Set；
- hard negative；
- retrieval metrics；
- path/structure metrics；
- context metrics；
- answer metrics；
- latency/cost/storage；
- source-specific correctness；
- release gates。

## 9. 实施

- 当前代码映射；
- 新模块；
- schema migration；
- Feature Flag；
- sprint/tasks；
- test plan；
- acceptance；
- dependencies。

## 10. 面试同步

必须同时更新对应的 `interview/*` 文档：

- 当前问题和设计取舍；
- 一张白板图；
- 一次成功消融；
- 一次失败实验；
- 一次性能/成本取舍；
- 真实指标占位表；
- 简历 bullet；
- 高频追问；
- 可定位的代码和测试。

