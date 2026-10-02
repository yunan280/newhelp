### Task 10：前端 Vibe Coding 与真实点击

**Files:** Modify `src/mewhelp/static/index.html`；浏览器证据保存 `artifacts/ch06/<run-id>/ui/`。该任务不做前端 brainstorm、TDD 或 code review。

**Interfaces:** 使用 Task 7/9 固定 API；聊天流处理 `order_selection/waiting_for_order`，点选后 POST selection/stream 并继续同一业务消息；已完成回复接收 refund_offer 并呈现表单。

- [ ] 直接实现消息内订单卡片与加载/失效/选中状态，点选自动回填、不另发一条“我要退款”从头分类。等待状态可继续发文字；刷新通过 pending 接口恢复卡片，仅客户端旧缓存不得开放操作。
- [ ] 直接实现订单只读、固定原因下拉（初始空）、提交/取消和申请号回执；失败保留选择，提交中防重复，同 offer 重试。取消不写库，成功文案明确待审核。
- [ ] 在真实运行服务的浏览器完成：无号退款→点选→答案/引用；等待时改问物流→旧卡片失效；刷新恢复卡片；表单取消、选原因提交、刷新回执。截图与实际 session/申请号关联保存；URL 控制受限时报告真实限制，不用伪造页面证明。
- [ ] 只做必要语法/现有页面 smoke 辅助检查，不新增前端 TDD 或独立 review。追记用户可见效果与点击结果，提交 `feat(ch06): add inline order selection and refund application form`。

