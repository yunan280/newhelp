# Ch05 冻结样例与真实评估

`intents.jsonl` 在实现前冻结28条七类意图（每类4条），`agent-decisions.jsonl` 冻结12条工具/追问/独立选项与禁止业务承诺场景。后者的 observations 是预先标注的工具数据，用于测试模型看到中间结果后的下一决策，不声称这些数据是线上商品事实。

分类比较枚举，决策比较必要工具参数、answer/clarify 及动作集合；最终正文另检查虚构业务动作并逐例人工阅读。措辞不做逐字匹配。纯 Prompt 使用这些标签跑真实提供商，不用字符串单测替代。脚本的失败处理、汇总和 HTTP 验收器则用代码单测验证。

```powershell
$pyCh05 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
& $pyCh05 -X utf8 -m mewhelp.ch05.evaluation --dataset eval/ch05 --part all --outdir artifacts/ch05/<新的run-id>/prompts
```

结果目录必须不存在，避免覆盖失败尝试。每例保存输入/expected/actual/raw/用量/调用数/响应模型/失败原因；服务错误也导致非零退出。hash 包含样例、prompts.py、intent.py、config.py、请求模型名，决策另含 agent.py。最终输入构造、Prompt 或配置变更必须补跑相应部分；未变部分可由 `--reuse-intents` / `--reuse-decisions` 校验完整标签、实际结果和 hash 后汇总。

最终分类 `artifacts/ch05/ch05_20261001_01/intents-final` 为28/28；决策 `decisions-attempt2` 为12/12，均服务错误0。实际请求 deepseek-chat，响应 deepseek-flash；本地固定问候用量/调用数为0。`prompts-final-verified/summary.json` 是最终哈希校验汇总。旧尝试保留，不改 expected 迎合输出。这些小样例证明本章场景，不能推断所有真实客服问题均正确。

真实 HTTP 验收另运行 `scripts/smoke_ch05_acceptance.py`，同一冻结六场景分别请求 JSON 和 SSE。复杂场景要求订单观察之后下一决策轮才查物流；弱证据要求检索闸拒答、Agent=0、MySQL问题池提交。工单仅在脚本显式 confirmed=true 后新增一张带测试描述的单，重发复用原号。
