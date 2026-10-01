# 邮费 JSON 回归独立复核

基线：42d3252；只读 reviewer：shipping_json_review，无业务写入、无模型调用。

第一次结论：1 Important，不能部署。无条件 JSON mode 实测使物流和订单样例不调用
业务工具；冻结评估10/12、服务错误0。请求 mock 不能证明供应商工具行为。

用户明确批准有界纠正方案后复核：原 Important 已解决，未发现新的真实重大缺陷。
常规决策恢复原 Function Calling；JSON mode 仅在非法非工具控制返回后用于一次无工具
纠正。纠正前重新检查步数、期限和含最终回复预留的总 token 预算；累计调用与用量。
纠正仍非法或意外带工具调用即固定结束；基础设施异常不在格式异常捕获范围内。

reviewer 独立核对真实工件：decisions-shipping-correction 12/12、服务错误0；
原物流/订单失败样例实际调用工具。Ch05 102 passed，全量638 passed、5 deselected。
本条审查阻断解除。真实 HTTP 邮费/物流复测仍由主代理完成后报告。
