# Ch03 知识库实施计划

Spec：`../specs/2026-09-28-ch03-knowledge-design-draft.md`

执行规则：每项先给出行为测试或标注评估，再实施，再运行验证，最后追记 `dev-notes/ch03.md`。所有具体库接口先核对官方文档和本机安装版本。保留 `query_faq(keyword: str) -> str`。

## Task 1 · 运行环境和接口探针

- 建新虚拟环境，安装项目与 rag/dev 依赖；记录精确版本。
- 实测 BGE-M3 dense 输出形状、MilvusClient 创建集合/upsert/search 的形状。
- Docker 启动 MySQL 和 Milvus；不改变指定技术选型。

## Task 2 · 结构感知切分

- 先测标题路径、递归超长、句号重叠、大表复制表头、前后指针和超限单句。
- 实现纯 Python Markdown 切分；示例文档放 `knowledge-docs/`。

## Task 3 · MySQL 原文模型和幂等写入

- 严格采用用户提供的 `knowledge_chunks` 和 `qa_extraction_staging` DDL，ORM 与漂移验证。
- 先测来源位置映射到确定性 `id` 后重跑无重复、正文变动重置 `vectorize_status`、旧块撤下。
- 保持 MySQL 为原文权威源。

## Task 4 · BGE-M3 与 Milvus 双写

- 先测 MySQL 已提交后中断、Milvus upsert 后中断、补偿重跑。
- 1024 维 dense、固定相似度指标；Milvus `knowledge` 主键等于 MySQL 主键，`vector_id` 字符串回填。

## Task 5 · FAQ、文档和对话建库 CLI

- FAQ 取真实问法；政策手册按章节标题和上级路径填字段。
- 对话按批次抽取到权威暂存表，全批结束后统一去重和排除个人信息，再发布。DDL 无任务水位，每次重新扫描，按确定性知识主键幂等。
- CLI 含建库、补偿、对话挖掘入口，提供系统计划任务示例。
- 对 Prompt 与数据产物用标注样例评估，不写只镜像实现的单测。

## Task 6 · 在线 `query_faq`

- 先测工具契约不变、语义召回「邮费是多少」、空结果防臆答、MySQL 状态过滤。
- 将工具内部切换为 BGE-M3→Milvus Top-K→MySQL 原文；旧关键词查询不再参与在线路径。
- 更新上一章以“邮费必漏”为前提的测试与文档描述。

## Task 7 · 系统验证与收尾

- 跑单测、离线标注评估、真实 MySQL/Milvus/BGE-M3 演示，注入中断再重跑。
- 两轴 code review：对照仓库规范与本章需求；修复发现项。
- 追记 review 和 finish，交付功能演示命令、测试结果、留痕路径。

## 计划评审要点

1. 契约固定，变更只在工具内部和离线流程。
2. MySQL 先提交再向量化，补偿扫描覆盖两处中断窗。
3. 应用层来源键映射成确定性 BIGINT 主键，与 Milvus 主键支撑幂等；`pending`/`done` 状态过滤避免过期原文输出。
4. 纯 dense 单路，元数据不进入向量文本。
5. 暂存全批去重后发布；任务水位在发布成功后推进。
