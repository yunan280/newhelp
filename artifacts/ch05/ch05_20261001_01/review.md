# 独立整分支审查

审查：gpt-6-astra / fresh context / read-only，范围 a2a98a8..0bfe640。无Critical，3 Important / Medium，1 Minor / Low，结论 Ready to merge: With fixes。未调用真实模型、写业务工单或操作服务。

优点：固定路由和只读工具边界明确，跨轮State清空、完成历史与本轮轨迹分开，工单有数据库唯一键和实际行校验。安装接口、最终Prompt hash和acceptance-verified的14/14已核对。

1. Important：`src/mewhelp/ch05/agent.py:195`，流式最终答复达到max_tokens并返回finish_reason=length，仍标completed/clarification，保存残缺正文，页面开放正常反馈。无模型探针“退款条件包括以下三项：第一，”+length复现。须明确输出限额，SSE正文与保存答案一致。
2. Important：`src/mewhelp/static/index.html:609`，描述/类型编辑后确认、数据库已提交而响应丢失，刷新恢复原offer参数，同offer_id重试被正确拒绝409，用户拿不到号。实际HTML探针“具体问题：订单1001损坏 / 售后”刷新回到“我要投诉 / 投诉”。须在请求前保存完整已确认参数，不确定结果后恢复同一请求。
3. Important：`src/mewhelp/ch05/evidence.py:32`，现有reranker抛UnsupportedContextError（8192 token限制），异常绕过gate和拒答入池，返回服务错误。旧answer_question已处理同类型。仅将此能力边界转unsupported_context_size，真实基础设施错误仍传播。
4. Minor：`src/mewhelp/ch05/workflow.py:76`，分类前reserve_call超限直接返回泛化502 / BudgetExceeded；默认“退货政策”重复6000次，无模型调用但缺友好限额提示。建议后续统一明确提示。

Declined to judge：

- 冻结样例之外真实模型输出的语义可靠性，本次不调用真实模型。
- IAB原生人工确认与两种顺序的真实浏览器成功行为，已有记录保留限制。

历史acceptance-final目录为9/14的第二次失败尝试，正式acceptance-verified为14/14；README正确引用后者，未列为缺陷。根代理负责定级、一次TDD修复pass及裁定，结果见review-resolution.md。
