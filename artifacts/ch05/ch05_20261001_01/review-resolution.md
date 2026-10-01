# Native 唯一修复 pass

根代理重新按用户影响定级，接受三项Important。无第二位reviewer/复审；每项由失败回归证明修复。

| 发现 | RED | 修正与GREEN |
| --- | --- | --- |
| length误报正常完成 | `test_truncated_answer_stream_checkpoint_and_ledger_agree` 失败，实际completed | 读取SDK聚合finish_reason，附明确“不完整/限额”提示，stop_reason=output_limit，仅建议人工；SSE完整文本、checkpoint和MySQL一致，页面及刷新不开放普通反馈。节点/图测试通过，Node场景G通过。 |
| 确认工单参数刷新丢失 | 实际HTML场景F编辑描述/类型、响应丢失、刷新后原参数恢复错误，重试409 | 请求前存完整ticket_request，不确定回执后的表单与主动重试沿用同一参数；确认后参数锁定，刷新不自动fetch；取回原号只写一单。Node场景F通过。 |
| reranker超长绕过gate/池 | `test_reranker_oversize_goes_through_gate_and_commits_before_token` 抛实际UnsupportedContextError | 仅捕获此异常构造unsupported_reason，仍走正式gate，原问题在首个token前独立提交unsupported_context_size；Agent=0。真实基础设施异常回归仍明确报错。 |

合并回归39 passed；全量627 passed、5 deselected，28.46秒，Ruff与32文件格式检查通过，Node七组ALL OK。一次新增测试导入缺空行被Ruff指出，补空行后全检查通过。

最终agent.py改变，因此同12条冻结expected重新真实评估：`decisions-post-review` 12/12、服务错误0，hash=6e5614f7b7fed7140046f2a420db376779a8f5680b7fdbdc659adbb9f0ec0874；逐例最终正文再阅读，无假称执行/到账/送达承诺。意图代码/Prompt/hash未变，复用最终28/28，`prompts-post-review`严格校验完整标签和hash。更新后的真实JSON/SSE六场景+独立确认/重发14/14、错误0，`acceptance-post-review`。复跑新增明确确认测试单一张，总6张（原1+测试5），旧单保留。

最终真实页面两个独立按钮截图ui-final.jpg；未额外确认工单。IAB原生人工确认限制仍照实保留，Node已覆盖取消、两种顺序和继续聊天。

延期Minor：分类前极长输入或过小预算返回泛化502/BudgetExceeded；零模型调用、无业务动作，用户提示不足。未冒称此项已修复，Native规则要求Minor记账交用户决定。

Declined to judge裁定：R8保留最简模型版本，40条样例不能证明新问法语义可靠性；R9保留R6的真实原生人工确认缺证界限，常规浏览器仍需人最终核验。完整裁定与任务记录见native-process.md。
