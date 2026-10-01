# 知识库录入页实施计划

依据：[设计稿](../specs/2026-09-29-kb-entry-page.md)。

## 评审结论

通过。复用 `KnowledgeDraft`、`put_chunk`、`sync_pending` 和现有向量适配层；HTTP 测试只观察页面与 JSON 接口。特别核对了两点：Milvus 连接必须发生在 MySQL 提交之后；单条重试不能被其他待处理行抢占批次。为此给 `sync_pending` 增加可选主键过滤，保留现有 CLI 全量补偿行为。

## 任务

1. TDD：`GET /kb` 返回可录入页面，页面包含必填字段和知识列表容器。
2. TDD：`POST /api/kb/entries` 校验输入、按 UUID 稳定主键保存；`GET /api/kb/entries` 可看到结果。向量化失败时响应为 `pending`，原文仍可读。
3. TDD：新增单条补偿过滤及 `POST /api/kb/entries/{id}/sync`；成功回填 `vector_id` / `done`，重复提交不增加新行。
4. 完成页面交互与状态、错误展示；执行聚焦及全量测试，并用运行中的服务打开 `/kb` 检查页面。按阶段追记 `dev-notes/ch03.md`。

## 验收

- `/kb` 可打开；可以录入一条知识并看到状态。
- 失败时 MySQL 行保留 `pending`；重试只补齐该主键。
- 现有 `query_faq` 契约和 ch03 离线流程回归通过。
