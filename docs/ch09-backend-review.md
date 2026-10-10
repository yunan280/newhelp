# Ch09 后端审查记录

2026-10-10交付。一次全新gpt-6-astra后端只读审查，范围c1f8672c14c975d22a697c099be81c6d9a7d91a2..5c9881c；排除前端视觉代码，遵守用户Vibe例外。审核代理在消息中报告审查完成，4项Medium、无High。正式长报告发送时额度中断；以下保存已收到的具体反馈及作者验证，不代表取得完整正式报告或修复后独立复审。

| 已收到反馈 | 触发与影响 | 单次修复及验证 |
|---|---|---|
| flywheel.py SQL线程取消提前释放队列锁 | await to_thread被取消，线程尚在commit却已退出named lock，另一个worker可能并发归并 | threads.settled_thread等待实际线程结束再抛取消；真实线程/重复取消失败测试RED→GREEN，锁保持到提交结束 |
| evaluation_jobs.py 已完成轮重启后resume冲突 | 新manager重跑完成记录收尾，elapsed_ms变化造成不可变metrics冲突 | 已有eval_run_id直接返回原持久回执，不重跑模型；新manager+持久回执测试RED→GREEN |
| evaluation.py 非法结构/引用被记作普通拒答 | parsed=None或invalid_citation，生成器兜底后errors仍为0，混淆故障与正常证据不足 | AnswerResult保留refusal_reason_code；评估保存GenerationFailure与error_stage，合法不足仍无故障。实际正式生成函数样例2 RED及合法不足GREEN，修复后GREEN |
| 旧轮自动取消混入新轮trace | 新消息先取消旧interrupt，旧graph节点与新消息共用root | 两种自动取消分别创建独立root，保留旧turn/session/intent/origin，再恢复新上下文。订单和工单两个边界RED→GREEN |

作者另发现JSON终态消费aclose把已completed/waiting root覆写成disconnected，以及Ch02新建会话未将真实身份bind到root。3项失败测试RED→GREEN；本次真实会话162、163、164的三条独立trace均completed，身份已正确绑定。

所有修复提交97b0df6。最终完整1120 passed、20 deselected；受影响真实MySQL6 passed；末轮相关273 passed。依据Native执行技能仅一次修复、不派第二名审查者。已收到反馈中无延期Minor；完整报告中的Declined to judge清单因额度中断未获得，不能声称全量为零。记录与日志见dev-notes/ch09.md及artifacts/ch09/20261008-native/verification/。
