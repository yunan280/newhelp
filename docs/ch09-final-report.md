# Ch09 交付报告

2026-10-10。功能代码交付提交97b0df6，分支`codex/ch09-observability-flywheel`，独立工作树`C:/Users/27497/projects/mewhelp-wt/ch09-observability-flywheel`。后续提交保存本报告、执行账本与证据检查脚本；最终提交可用`git rev-parse HEAD`查看。用户随后要求「推送到github上」，已将该分支推送到[yunan280/newhelp](https://github.com/yunan280/newhelp/tree/codex/ch09-observability-flywheel)。工作树和运行服务保留，未合并主分支，原Ch08工作树未改动。

聊天：http://127.0.0.1:9030/；审核：http://127.0.0.1:9030/review；统计：http://127.0.0.1:9030/ch09/stats；自部署Langfuse：http://127.0.0.1:3039/。原Ch08仍在http://127.0.0.1:9020/。Langfuse登录凭据只在本机忽略文件`.env.ch09.langfuse`，不在报告中公开。

## 已实现

- 本地Langfuse官方回调在LangGraph编译时绑定，独立请求root；知识检索/精排、工具、模型Prompt与实际provider用量进入同一树。resume、旧轮自动取消分别保留origin关联。遥测故障不改变业务结果。
- 以校准20题产生正式evidence_confidence阈值，冻结后用40题test评估；知识类检索之后、Agent之前执行同一闸。业务FAQ工具带回原始证据也经过该闸，不能绕开。
- 检索低置信、生成自评不足、持久回答👎均进入问题池；保存该轮原始Top5片段、评分与入口。未检索为SQL NULL；历史无法确定的状态明确保留unknown。
- 标准化、示例答案和语义查重进入review_queue；命中待审缺口原子累加次数并登记matched_review_id。专用autocommit named lock保护语义归并，没有SQL事务跨模型调用；取消等待提交线程结束。
- 审核页能看归并原话/快照、修改核准答案、通过或驳回。通过复用Ch03入库/同步，强发布回执核对Milvus内容hash，失败可重试；列表SQL indexed与强published回执分开。
- 隔离生产检索/生成/判定路径评估，eval_runs一轮一行，完整性/配置hash/NA/分母和同口径趋势均保存；完成轮重启后resume返回原回执，不重复模型调用。
- 按最终意图汇总真实generation input/output token，排除后台评估/飞轮和父链聚合，未知用量不猜；Windows每周日04:00定时评估已登记。

Schema依照用户DDL新增review_queue、eval_runs以及问题池外键/快照，并按用户确认增加消息快照和反馈入口。未引入Skill机制、主题微调分类器或额外业务系统。

## 六项验收的实际证据

| 验收 | 实际结果与证据 |
|---|---|
| Langfuse完整链路 | 已在真实UI展开知识请求的Prompt和原文检索节点，保存langfuse-prompt.png、langfuse-retrieval.png；最终导出20条完整observations，包括知识、拒答、MCP、确认/取消、resume和并发。修复后旧入口与两个并发JSON请求的3条新root均completed且身份正确 |
| 缺知识→待审详情 | 会话153首次询问「星尘验收体验包」免费延保证明，置信度0.459779低于0.743871，返回兜底；原话池29→review10，实际页面展开原话和当轮Top5评分，review-origin-snapshots.png |
| 人工通过→同问答对 | 页面填写明确仅本地演示的核准答案并通过，知识ID6407333891965568792；同一原问题再次询问，回答881正确给出演示订单编号与蓝色验收卡并引用[1]。过程中未修改核心代码或重启服务，flywheel-approved.png、flywheel-closed.png |
| 👎落池、快照、查重 | 回答859实际点击👎，池25/26归并review7次数2；刷新及切会话恢复反馈，不重复计数。另业务回答869的👎入池28→review9，确实未检索，快照SQL NULL。生成不足池27→review8并实际驳回，三个入口均有真实记录 |
| 按意图token花销 | 最终统计UTC[2026-10-08,2026-10-11)有6类标签、21个请求、真实generation用量覆盖率100%，见下表及final-evidence.json。旧入口使用原始business标签，未强猜细分意图 |
| 两轮评估与趋势 | eval_runs.id1/2，两轮各真实执行40题，结果文件各40行；同口径可比，六个指标delta均为0。实际没有下降，也没有改造数据制造改善 |

确认/取消兼容验收亦已完成：会话157先「帮我建个工单」追问，补描述后预览，刷新恢复后确认，tickets1、T20261009001、audit30成功/retry0/31ms；会话161点击取消，tickets0、audit31权限拒绝/retry0。ticket-preview-restored.png、ticket-confirmed.png及DB回执保存。主图与旧入口均实际通过MCP查询物流；没有重复内置query_logistics。

全部原始文件在`artifacts/ch09/20261008-native/acceptance/`。final-evidence.json为只读导出，含15个会话、20条trace、4条review详情、统计和趋势；postfix-cases.json/postfix-roots.json是末轮修复后的3个新请求。既有失败请求、旧root的错误disconnected状态均原样保留，没有重写历史遥测。

## Langfuse请求链接

- [真实缺知识请求、Prompt/检索UI展开](http://127.0.0.1:3039/project/mewhelp-ch09/traces/4217b1f19d8f74e61cedaabcedd6f57f)
- [飞轮审核通过后同问正确回答](http://127.0.0.1:3039/project/mewhelp-ch09/traces/fedb3420d513e771d9eada2392bb9cbc)
- [修复后的旧入口MCP与会话身份](http://127.0.0.1:3039/project/mewhelp-ch09/traces/dc183b5f1f338d32458a68cf2dd40f34)
- [工单确认resume](http://127.0.0.1:3039/project/mewhelp-ch09/traces/84252ac757455039a71027c559aaac1f)
- [工单取消resume](http://127.0.0.1:3039/project/mewhelp-ch09/traces/801cfd1b2a7f0e761efc1f877f83953e)

## 校准与两轮真实评估

正式配置`config/ch09-confidence.json`：TopK5；有效分数cutoff0.003920442890375853；权重Top1/有效证据数/gap为0.75/0.25/0；阈值0.7438707113265991。gap信号保留，校准所选权重为0，不人为强加作用。校准20题误放0、误拒1；40题test不参与阈值选择。生成标注v2实际12/12、标准化查重28/28、路由新增样例16/16，均有早期分阶段记录。

| 指标 | r01 | r02 | 分母/说明 |
|---|---:|---:|---|
| 候选Recall@50 | 1.0000 | 1.0000 | 32条有目标证据的题 |
| 候选MRR@50 | 0.984375 | 0.984375 | N32 |
| 最终Recall@5 / @10 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 各N32 |
| 最终MRR@10 | 1.0000 | 1.0000 | N32 |
| Faithfulness | 0.988506 | 0.988506 | 实际生成并判定29题 |
| 回答覆盖率 | 72.5% | 72.5% | 29/40，不以部分生成分数代替全量效果 |
| 正确拒答 / 误拒 / 误放 | 8 / 3 / 0 | 8 / 3 / 0 | 原始40题 |
| 运行错误 / judge错误 | 0 / 0 | 0 / 0 | 两轮相同 |
| 实际耗时 | 327157ms | 344681ms | 两次独立运行 |
| 生成input / output | 59395 / 4940 | 59395 / 4878 | 实际provider token |
| 判定input / output | 41756 / 4616 | 41743 / 4530 | 不混入在线意图成本 |

两轮均有三个误拒：colloquial-04、colloquial-08、synonym-12；这是保留的质量不足，未通过重复评估或改test标签掩盖。comparison_hash一致，result_hash和输出token不同；两次实际运行，未重用答案。末轮错误分类/恢复修复不改变Prompt、模型、阈值或指标数学；审计全部80条原始记录，各轮33条正式结构评估均有效且引用在范围内，未发现非法生成/引用。没有重跑已通过的80题。

产物：`artifacts/ch09/20261008-native/evaluations/ch09_20261009_r01/`与r02，包含cases.jsonl、summary.json、manifest、隔离语料/检查点及收尾回执。

## 实际意图token统计

统计窗口UTC[2026-10-08T00:00:00Z,2026-10-11T00:00:00Z)，抓取于2026-10-10T04:17:07Z。总87389 token；当前窗口未知用量0，每类generation覆盖率100%。此结果包含验收时正常、拒答、失败和resume请求的真实模型消耗，不只取成功答复；无模型调用的请求为0。不同窗口可能有unknown，会明确显示。没有提供真实价格，因此不估货币金额。

| 最终意图 | 请求数 | input | output | 总token | 每请求均值 |
|---|---:|---:|---:|---:|---:|
| 售后 | 7 | 24731 | 700 | 25431 | 3633 |
| 商品咨询 | 4 | 23166 | 684 | 23850 | 5962.5 |
| 其他 | 3 | 12700 | 275 | 12975 | 4325 |
| 闲聊 | 4 | 10888 | 216 | 11104 | 2776 |
| 物流 | 1 | 7528 | 195 | 7723 | 7723 |
| business（旧入口原始标签） | 2 | 5852 | 454 | 6306 | 3153 |

当前总量售后最高；每请求物流最高，但只有1个请求，不能据此推广成稳定成本结论。这里复现真实当前样本，不推导无依据的费用预测。

## 测试、审查和实际限制

| 验证 | 结果 |
|---|---|
| 功能代码97b0df6完整单元回归 | 1120 passed、20 deselected，148.72s |
| 末轮受影响真实MySQL | 飞轮/评估登记6 passed，56.58s；此前本章DB13项及快照/反馈7项亦有分阶段实际通过日志，不能简单相加当唯一用例数 |
| 后端末轮相关回归 | 273 passed，26.87s；恢复28 passed；终态/身份29 passed |
| 本章ruff | 模块、测试、迁移/导出/交付检查脚本通过；没有宣称旧项目全部lint通过 |
| 依赖兼容 | uv pip check133包通过 |
| 前端 | 4份JS语法与3条HTTP页面路由通过；另外的真实浏览器动作有截图，未用前端TDD/审查冒充Vibe流程 |
| 交付证据检查 | scripts/verify_ch09_delivery.py核对80条原始记录、DB回执、独立trace、三个服务HTTP200，模型重放0 |

一次独立后端gpt-6-astra审查已反馈4项Medium、无High；四项均经失败测试复现并在同一次修复中处理，详情[后端审查记录](ch09-backend-review.md)。正式长报告因代理额度中断，未获得完整Declined to judge清单，没有修复后再次独立审查，不宣称完整签字报告已通过。已收到反馈中无延期Minor。

最后统计页浏览器检查因Computer Use无法可靠识别当前浏览器URL被策略停止，已停止UI输入。此前真实审核/聊天/Langfuse页面证据保留，当前两轮趋势和统计以实际API导出为证；不将本次API成功改称新的浏览器页面验收。用户可直接打开上方统计页验证。

定时任务MewHelp-Ch09-Evaluation为Ready、已启用，下一次2026-10-11 04:00 Asia/Shanghai；尚未日历触发，不伪称已运行。依赖当前用户登录、Docker及9030可用；失败非零并留日志。Ch03定时任务保持不动。

恢复Docker时只可逆改名两处残留运行目录，未清除数据卷或恢复出厂；备份后缀ch09-recovery-20261010-120721。全部原始证据和测试日志留本地，不提交凭据、trace原文或数据库。

## 演示与验证命令

完整部署/服务命令见[演示命令](ch09-demo.md)。已启动的9030可复用，启动脚本会核对端口归属，不会杀其他进程。

```powershell
Set-Location C:\Users\27497\projects\mewhelp-wt\ch09-observability-flywheel
powershell -NoProfile -File scripts/run_ch09.ps1 -Port 9030
# 只读检查现有交付证据，不请求模型、不重跑评估
./.venv-ch09/Scripts/python.exe -X utf8 scripts/verify_ch09_delivery.py
Invoke-RestMethod 'http://127.0.0.1:9030/api/ch09/eval-trends'
Invoke-RestMethod 'http://127.0.0.1:9030/api/ch09/token-costs?from=2026-10-08T00:00:00Z&to=2026-10-11T00:00:00Z'
# 需要新评估时给新run_id；当前交付不需再跑这条
powershell -NoProfile -File scripts/run_ch09_evaluation.ps1 -RunId ch09_manual_20261010_01 -TriggeredBy 手动
```

已入库的闭环演示问题：`请查知识库：本地演示商品「星尘验收体验包」办理免费延保需要哪些凭证？`。验证新的审核闭环应使用另一条明确限定本地演示范围的问题和人工核准规则，不能编造真实商品政策。

过程留痕：[dev-notes/ch09.md](../dev-notes/ch09.md)。完整决策和各任务完成记录：[执行账本](ch09-execution-ledger.md)。测试全日志和最终delivery-check.json在`artifacts/ch09/20261008-native/verification/`。设计/计划在docs/superpowers/specs和plans中，以2026-10-08-ch09-observability-flywheel命名。
