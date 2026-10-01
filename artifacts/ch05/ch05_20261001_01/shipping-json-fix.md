# 邮费问题的决策格式回归

用户原话：`这是什么情况`；截图输入 `邮费是多少`，页面报 `ValidationError`。

根因证据：2026-10-01 16:05:07–16:05:40 的 8005 日志显示
`retrieve_knowledge → confidence_gate → agent_decide`。`agent.py:120` 将模型返回的
`邮费规则如下：…` 正文直接交给 `AgentDecision.model_validate_json`，触发 `json_invalid`。
提示词要求 JSON，但请求未设置供应商 JSON 输出约束，且校验失败没有有界结束分支。
原开发记录曾记录同类现场失败；当时只保留错误，没有修复格式边界，这是遗漏。

Context7 本次核对：LangChain Reference 的 Runnable.bind / ChatOpenAI 透传参数，
DeepSeek 官方 JSON Output 与 Chat Completions 的 `response_format={"type":"json_object"}`。
官方说明 JSON 模式仍可能空响应或因 length 截断，因此不能移除应用侧严格校验。

初版实验边界（后因真实工具回归而撤回）：只约束 Agent 的非流式控制请求；三个原业务读工具继续可选，最终正文继续流式。
不合法控制结果不得作为答案、动作或工具执行，保留用量并走固定回复，只建议人工选项。
不额外重试、不换框架/模型、不扩展业务工具或放宽 schema。

- [x] RED：HTTP 请求缺 JSON 约束；非法控制数据的节点和完整图回归。7 failed，缺失参数断言和原始 ValidationError 均符合预期；证据 `process-evidence/shipping-format-red.txt`。
- [x] GREEN：控制模型工厂通过 extra_body 发送 JSON 输出，具体捕获校验错误并有界结束。98 passed；证据 `process-evidence/shipping-format-green.txt`。
- [x] 验证：用户确认后的最终修正全量638 passed / 5 deselected；原12条冻结决策12/12；真实纠正提示词5/5；真实邮费/工具HTTP4/4。第一次634通过但模型10/12的实验报告保留。
- [x] 独立复核解除Important阻断，已重启8005并确认健康接口/首页200；服务保留运行供用户测试。

API 修正过程：第一次把 response_format 传 bind_tools，实际 1.6.6 把它当 JSON schema
转换而失败；第二次传 ainvoke 顶层触发 OpenAI SDK parse，要求 strict tools 而失败。
没有更换 SDK 或工具 strict 模式，改为沿用已验证的 extra_body 供应商参数路径。
初版 HTTP MockTransport 实证请求同时包含 JSON mode、三个原非 strict 读工具及原
max_tokens/thinking；最终正文请求没有 JSON mode 和 tools，仍为 SSE 文本。

第一次真实评估阻断：`decisions-shipping-fix/results.json` / `summary.json`，服务错误 0，
simple-logistics 与 simple-order 缺必要工具调用；两者第一条 raw 为 answer JSON，
tool_calls=[]。完整单测全绿不能代替供应商行为验证。该初版实验未提交、未重启部署。

按用户「实现中发现矛盾或走不通，停下来问我，不要自行换方案」要求，先请求确认：
保留原 Function Calling，只在没有工具调用但控制 JSON 不合法时，做最多一次有界的
JSON 控制纠正；失败固定兜底。用户随后明确回复「按有界纠正方案继续（推荐）」，才修改协议。
原框架/模型/业务工具保留。原10/12报告没有改称通过；最终结果单独保存在下列路径。

## 最终证据

- `process-evidence/shipping-correction-red.txt`：新控制纠正行为10失败，1项误通过后补准确断言另实测RED，开发记录说明。
- `process-evidence/shipping-correction-green.txt`：Ch05 102 passed。
- `process-evidence/shipping-correction-full-suite.txt`：638 passed、5 deselected。
- `decisions-shipping-correction`：原12条冻结标签，12/12、服务错误0。
- `shipping-correction-prompt-cases.jsonl` / `shipping-correction-prompt-results.json`：先冻结5条修正标签；初始非法控制输出注入，纠正调用为真实供应商，5/5、服务错误0。不能将注入的第一条称为真实供应商现场。
- `shipping-correction-live/summary.json`：真实服务JSON邮费、SSE邮费、物流工具、先订单后物流4/4；后者工具round1/round2，最终完成。
- `shipping-correction-review.md`：原Important解决，无新增重大缺陷。

最终协议：常规决策保持原 Function Calling；只有非法非工具控制输出触发最多一次
无工具 JSON 纠正，重新检查步数/用量/期限并保留最终回答预算。纠正仍失败则固定兜底。
真实邮费回答：单笔订单实付满99元包邮；不满99元配送费8元起，引用[1][2][3]。
最终服务仍在 http://127.0.0.1:8005/ 运行，沿用原持久化文件，不创建测试工单。
