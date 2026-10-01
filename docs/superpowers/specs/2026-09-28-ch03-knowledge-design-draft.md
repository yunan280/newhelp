# Ch03 知识库设计（brainstorm 定稿）

状态：按用户确认的替代流程定稿。具体库接口依据最新官方文档核对；当前会话无 Context7 MCP。

## 目标与边界

- 保持 `query_faq` 的工具名、`keyword: str` 入参及字符串出参。内部改为 BGE-M3 dense 向量检索，Milvus 取 Top-K，再从 MySQL 权威原文表读取结果。
- 本章只做 dense 单路；不引入关键词召回、混合检索和重排。
- 离线来源有 Markdown 文档、现有 FAQ，以及历史客服对话抽取的问答。首批使用仓库 FAQ 种子及示例文档；系统计划任务调用 CLI 执行对话挖掘。

## 知识单元

每条记录含 `category`、`questions`、`answer`。向量文本使用带字段标签的稳定拼接格式，并固定版本，避免重跑时同一条记录的向量内容漂移。FAQ 与对话问答使用真实问法；政策和手册以当前章节标题作 `questions`，上级标题路径作 `category`。

元数据：章节路径、内容类型、关键条款标记、前后块整数外键指针。它们留在 MySQL，不进入嵌入文本。表结构以用户提供的 `sql/ch03-ddl.sql` 为准：不增加来源键和任务水位列。来源位置在应用层映射为确定性正 BIGINT 主键，`vector_id` 用 VARCHAR(64) 存 Milvus 主键字符串，状态只用 `pending`/`done`。关键条款由文档显式标记，切分器不臆测。

## 文档切分

1. 先解析 Markdown 标题树，按章节形成候选块，保留完整标题路径。
2. 超长段落递归按段落、句子边界切分；块间带重叠，重叠终点回退到最近完整句号。若单句仍超限，记录异常并采用明确的兜底策略，不能静默截断半句话。
3. 表格识别表头及数据行。大表按行分组，每块复制表头；若单行超限，记录异常。
4. 同一来源顺序块设置前后指针。规范化文档根目录、相对文件路径与章节内块序号在应用层形成确定性主键；三字段正文变化时重新向量化。文档更新时先将消失的旧块标为不可检索 tombstone，再删除 Milvus 向量和 MySQL 行；`sync` 也扫描 tombstone 补偿。旧向量在删除前由 MySQL 过滤。清理仅作用于当前文档根目录。

## 对话挖掘

定时入口每次读取现有历史会话，按消息批次重建上下文。LLM 分批抽取可独立回答、事实有据的问答及来源引用，先写 `qa_extraction_staging`。整批抽取完成后统一规范化、去重及过滤，再转换为知识单元。权威 DDL 无任务水位字段，因此每次定时运行重新扫描；确定性知识主键避免重复正式记录。涉及用户个人信息或冲突答案的问答标为 `discarded`，不入库。

## 双写与恢复

MySQL `knowledge_chunks` 是原文权威源。离线流程先按确定性 `id` 写入 MySQL，状态为 `pending`；随后生成 BGE-M3 向量，以同一 `id` 为 Milvus `knowledge` 主键做 upsert；成功后把主键字符串回填到 `vector_id`，状态改为 `done`。重跑扫描 `pending`，重复写同一主键只更新同一实体。若 Milvus 写入成功而 MySQL 回填失败，重试复用同一向量主键。更新正文时先把 MySQL 状态转 `pending`；在线只接受 `done` 行。旧 Milvus 向量即使短暂存在，也不会作为答案输出。

## 在线检索与验收

`query_faq` 将 `keyword` 当作用户问法生成向量，Milvus dense Top-K 返回知识主键与分数。工具用主键查 MySQL 原文，过滤非已向量化或不存在的记录，拼接成原有字符串式工具结果；无命中时保留明确的不可臆答提示。线上不依赖旧 `faq` 关键词查询。

验收用标注问题集覆盖「邮费是多少」→ 运费说明，以及负例、政策章节、表格行。再注入一次“写入 MySQL 后中断”，重跑证明待向量化块被补齐；还应覆盖“Milvus 成功、MySQL 回填前中断”。

## 决策与限制

1. 首批示例文档放 `knowledge-docs/`；现有 FAQ 种子同步到新库。
2. 对话挖掘以 CLI 为入口，由系统计划任务周期触发，仓库提供 Windows 任务计划程序示例命令。
3. BGE-M3 本地加载，使用其 1024 维 dense 向量；首次运行需要模型下载和足够内存。缺少资源时报告失败，不更换模型。
4. 问答去重按规范化问题与答案摘要全批处理；含订单号、电话、地址等个人信息或冲突答案的候选项标为 `discarded`。权威暂存表没有 category，正式知识统一归类为「客服对话」。
5. 单句或表格单行超过块上限时保留完整语义单元、记录超限诊断，向量化前按 BGE-M3 输入上限校验；无法容纳则保持失败状态并报告，不截半句。

## 官方接口依据

- [BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)：1024 维、dense 输出、最长 8192 token。
- [Milvus 创建集合](https://milvus.io/docs/create-collection.md)、[upsert](https://milvus.io/docs/upsert-entities.md)、[单向量搜索](https://milvus.io/docs/single-vector-search.md)：明确主键、向量字段、相似度及 Top-K 接口。
- [SQLAlchemy MySQL upsert](https://docs.sqlalchemy.org/en/21/dialects/mysql.html)：核对按唯一键幂等写入方法。
