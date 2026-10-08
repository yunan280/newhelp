# Ch08 最终报告

报告日期：2026-10-08。报告已整理交付，**整章尚未判定finish**：实现和自动化验证已完成，真实页面验收待用户结果；当前Docker故障阻止恢复客服和追加现场复验。

## 实现结果

| 需求 | 交付行为 |
| --- | --- |
| 统一注册中心 | 内置、可信本地插件、两个MCP来源同一ToolRegistry；每轮刷新目录和本地授权，新增工具无需改Agent或重启客服；内置query_logistics下线 |
| 参数校验 | 严格JSON Schema，无类型强转；非法参数以工具结果回灌，留审计；MCP原Schema保留，坏引用循环拒绝 |
| 权限 | 未知MCP默认拒绝，Server描述/annotations不能授权；本地配置热加载，本章外部写工具拒绝；create_ticket由引擎核验真实意愿及确认凭据 |
| 执行引擎 | 统一超时、暂时故障重试、分诊、中文结果；只读最多重试2次，业务空结果不重试，写操作自动重试0次；取消/异常仍落终态审计 |
| 审计 | 按原DDL创建tool_audit_logs：无外键、中文五状态、三索引、utf8mb4；独立最佳努力写入，审计失败不反向拦业务 |
| MCP | 官方Python SDK1.30.0，Streamable HTTP；官方MultiServerMCPClient/adapters0.3.2；独立物流与售后进程，纯mock无业务表 |
| 工单流 | 缺描述追问，原话提取，LangGraph interrupt持久化预览；确认后写tickets和真实工单号，取消拒绝审计；稳定幂等回执、丢响应只读恢复；原投诉按钮入口保留 |
| 页面 | 预览卡片及“确认提交”“取消”、防双击、刷新/会话恢复、迟到事件过滤、纯文本描述；按Vibe例外未做独立前端review |

固定技术选型未替换。具体接口先Context7核对；Prompt/数据采用冻结标注评估，代码采用有效RED→GREEN。执行方式为用户批准的Native：逐任务执行，末尾一次独立后端review，没有第二轮review。

## 测试与验收证据

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| 最终完整pytest | **966 passed, 5 deselected**，exit0 | `artifacts/ch08/20261007-01/process-evidence/full-suite-06.log` |
| Ruff | 全部通过 | 10月8日命令输出；此前`process-evidence/final-ruff.log` |
| 页面DOM | 预览8类场景通过，旧page-smoke和会话脚本通过 | `tests/ch08/page-ticket-preview.js`、`tests/page-smoke.js`、`tests/ch07/page-conversations.js`实际执行输出 |
| 真实模型验收 | **26/26**，保留通过结果，仅补失败/受影响样例 | `eval-04/summary.json`、`results.jsonl` |
| 最新路由校准 | 独立16条完成，阈值0.5；与验收分开 | `router-calibration-05/{results.jsonl,router.json,summary.json}` |
| DDL/MySQL | 原审计表中文ENUM、无外键、utf8mb4、三索引，SELECT1成功 | `environment/database.json`、HTTP evidence的SHOW CREATE |
| 六项HTTP/DB验收 | 全部通过，明确标为HTTP，未伪称浏览器 | `acceptance-http-02/{summary.json,evidence.json,responses.json}` |
| 消息配对故障修复 | 3个有效RED，14项相关GREEN，最终整套966通过 | `process-evidence/ticket-history-{red,green}.log`、`tests/ch08/test_ticket_history.py` |

上表原始模型/HTTP证据位于2026-10-07运行目录，属于已执行历史证据；10月8日新做的验证是完整回归和静态检查。5个排除项为仓库默认不跑的历史真实模型eval。根freeze覆盖54条标签/引用，不能表述为执行54条验收。

六项HTTP/DB验收的具体结论：

1. 新演示工具只做注册即被真实Agent调用，审计来源builtin。
2. 物流轨迹、在保、退货进度通过两个独立MCP返回；此项复用未受后续修复影响的`acceptance-http-01`实际记录，02报告标注来源。
3. Server新增query_delivery_window并补本地授权，仅重启物流Server，客服PID和核心代码hash均保持不变，真实调用成功。
4. 缺描述先追问；确认后tickets新增1，真实工单号**T20261007001**出现在回复。
5. 取消后tickets新增0，create_ticket审计“权限拒绝”。
6. 只读延迟最终超时retry2；隔离写故障探针有可信确认权限，仅执行1次、retry0，审计耗时完整，未产生真实新工单。真实建单写入另由第4项证明。

## 审查与返工

独立后端审查：0 Critical、6 Important、0 Minor。六项均通过执行方真实失败复现和一次修复验证：已提交回执的恢复顺序、终态旧建单意愿、MCP额外参数Schema、循环Schema/校验器错误审计、坏URL失败关闭、退避取消审计。完整结论见`docs/superpowers/reviews/2026-10-07-ch08-backend.md`。

随后现场补查发现额外消息协议问题：预览提示插入AI工具申请和ToolMessage之间，上游400拒绝下一轮。投影现只省略已完成建单轮的预览提示，原始checkpoint和页面流水保留；新增3变体测试先失败后通过。来源hash改变后实际执行校准05，未把旧校准改hash冒充新测量，也未重复26条验收。修复后的该现场HTTP补查仍待环境恢复，不能标成通过。

中断前full-suite-05只到29%，没有计为通过；10月8日full-suite-06完整补跑成功。各阶段四项过程记录见`dev-notes/ch08.md`；追加消息配对阶段的笔记因中断延后，记录明确说明补记及原始日志，没有伪造时间。

## 当前限制与待完成项

1. 用户已选择手动页面验收，但未反馈确认工单号、取消及刷新/恢复结果。真实页面确认/取消、切会话/重启pending恢复、原投诉按钮页面回归仍待结果；DOM和HTTP不代替这一验收。
2. 10月8日后台启动Docker后，Docker报告`initializing Inference manager ... dockerInference ... The file cannot be accessed by the system`。MySQL/Milvus未恢复，客服9020当前未启动；未执行重置出厂或删除Docker数据。
3. 物流9021与售后9022独立服务已恢复。模型配对修复后的追加HTTP检查尚未完成；上次成功读取原回执后在下一轮遇到400，该故障已由回归修复，但今天环境不足以再次现场证明。
4. 本章后端、脚本及报告提交到现有`ch02-tools`分支；前端HTML/对应DOM脚本保留未提交待手动验收。用户原有`dev-notes/ch07.md`不改、不纳入本章。未push、未合并、未创建PR；保留worktree、checkpoint、原始证据和计划ledger，等待剩余验收。

## 演示命令

在`C:\Users\27497\projects\mewhelp-wt\ch02-tools`执行。先修复Docker启动故障、确认MySQL/Milvus恢复，再运行客服；已有两个MCP进程无需重复启动。

```powershell
.\scripts\run_ch08_mcp.ps1 -Server logistics -Port 9021
.\scripts\run_ch08_mcp.ps1 -Server aftersales -Port 9022
.\scripts\run_ch08.ps1 -Port 9020
```

客服预期地址`http://127.0.0.1:9020`，MCP为9021/9022的`/mcp`。客服默认已指向最新校准05。详细演示输入、热注册样例、评估命令、审计SQL和精确限制见`docs/ch08-demo.md`。

## 执行裁定及代价（按记录顺序）

| 裁定 | 原因及代价 |
| --- | --- |
| PowerShell替代Bash任务记账 | awk缺失/MSYS写入受限；保留brief、BASE、测试日志及成功才完成。代价：记账实现差异 |
| builtin_permissions允许write/deny | 内置写工具也需热撤销/损坏配置失败关闭。代价：错误配置可暂时阻止建单 |
| PreparedToolCall增加来源字段 | 取消/拒绝也必须保留来源审计。代价：新增两个兼容DTO字段 |
| 每用户轮只处理一次建单请求 | 确认/取消后模型不能再次弹单。代价：多张工单需新明确请求 |
| DOM先于现场验证 | 真实服务依赖新校准。代价：前端保持未提交至现场验收 |
| 初次真实评估前扩展freeze | 保留原18标签，加入路由引用和8反馈标签。代价：manifest hash改变，未调整标签凑通过 |
| 32768窗口及实测Ch07 profile | 动态Schema需计入预算。代价：可用历史及潜在token消耗增加 |
| 新增只读回执GET | 刷新丢响应必须恢复号且不能自动重写。代价：一个新增读取端点 |
| 最终后端review与现场验证并行 | 实现结束即可独立审查。代价：后续故障纳入同一修复工作，不增加review轮次 |
| review排除前端 | 用户Vibe例外。代价：没有独立前端review，仍需实际页面验收 |
| review排除无关ch07笔记 | 保留用户既有修改。代价：该笔记不在本章审查范围 |
| 消息投影修复后重新校准 | 来源hash校验要求。代价：额外16条校准调用，保留26条已通过验收 |

延后Minor：无。本报告未以测试通过代替剩余验收，也未将环境错误归为功能已完成。
