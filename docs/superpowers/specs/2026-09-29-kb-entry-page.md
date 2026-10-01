# 知识库录入页设计

状态：用户确认 `/kb` 页面及录入、读取接口为对外边界（2026-09-29）。

## 用户流程

1. 打开 `/kb`，查看最近 50 条知识和向量化状态。
2. 填写分类、问法或章节标题、答案，以及可选的章节路径、内容类型和关键条款标记。
3. 提交后先把原文写入 MySQL `knowledge_chunks`；再尝试用现有 BGE-M3 / Milvus 同步逻辑发布。成功显示“已向量化”，失败保留“待向量化”并展示重试入口。
4. 对待处理条目可以重试同步；重试按知识主键 upsert，不生成重复向量。

## HTTP 边界

- `GET /kb`：录入页面。
- `GET /api/kb/entries`：最近 50 条知识及状态，供页面显示。
- `POST /api/kb/entries`：JSON 录入，包含客户端生成的 UUID `entry_id`；同一 UUID 重试更新同一知识主键。
- `POST /api/kb/entries/{id}/sync`：只补偿这条待向量化知识。

`category`、`questions`、`answer` 必填。`content_type` 限于 `faq`、`policy`、`manual`，默认 `faq`。所有元数据留在 MySQL，不进入向量文本。页面使用现有本地服务访问方式；不引入新的账户体系。

## 故障语义

提交已写入 MySQL 后，Milvus 或模型失败不回滚原文；响应和列表都显示 `pending`。重试继续按原主键同步。无效输入返回 422，不写库。已有 `query_faq` 契约不变。

## 官方接口依据

- [FastAPI 请求体](https://fastapi.tiangolo.com/tutorial/body/)与[文件响应](https://fastapi.tiangolo.com/advanced/custom-response/)：JSON 请求模型和页面返回。
- [FastAPI 同步路由](https://fastapi.tiangolo.com/async/)与[依赖覆盖](https://fastapi.tiangolo.com/advanced/testing-dependencies/)：同步数据库/嵌入调用在线程池执行，并以测试替身隔离外部服务。
- [SQLAlchemy Session API](https://docs.sqlalchemy.org/en/20/orm/session_api.html)：提交、刷新和事务边界。
- [Pydantic 验证器](https://pydantic.dev/docs/validation/latest/concepts/validators/)：去除空白和拒绝空内容。

当前会话无 Context7 MCP；按用户许可使用官方文档核对接口。开发环境版本为 FastAPI 0.141.1、SQLAlchemy 2.1.1、Pydantic 2.13.5。
