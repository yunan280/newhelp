### Task 12：后端 code review、必要修复与 Finish

**Files:** Review 所有后端/Prompt/标注/数据库差异与 spec；不将 `static/index.html` 纳入前端 code review。结论保存 `artifacts/ch06/<run-id>/{review.md,review-resolution.md,verification-results.json}`；Update `dev-notes/ch06.md`。

- [ ] 按最终选择的 Superpowers 执行技能派独立 reviewer，提交精确基线 `74e325b`、spec、计划、真实评估/失败证据，优先核对五个 Review Focus。Native 使用一次整分支后端审查，SDD 按任务审查后再总审。
- [ ] 当时追加 code review 结论；对有效后端 bug 先写能失败的回归再修，Prompt/数据问题用原冻结样例复验。固定技术矛盾或必须改设计的发现，说明具体证据并向用户询问。
- [ ] 修复改变哪些组件就重跑相应定向测试、评估和验收；结果 hash 或 Prompt 输入变化不得复用旧成功报告。最终重新确认全量测试/Ruff与必要真实场景，没有新变化和未解疑点不额外重复全套。
- [ ] 按 verification-before-completion 和 finishing-a-development-branch 完成留痕、提交与交付；不自动 merge/push/清理用户工作树。提供功能演示命令、实际测试/评估结果、报告、`dev-notes/ch06.md` 和真实限制；不把本地演示声称为真实退款支付系统。

## Spec Coverage 与计划自查

| 设计要求 | 实施/验证任务 |
| --- | --- |
| 完整问题透传、指代/改写合并、无跨 session 记忆 | 2、3、8、11 |
| 八类/两字段、few-shot、其他、默认主模型和可选升级 | 1、2、4、11 |
| 简单 FAQ 不扩，查询侧扩写、强制政策、去重重排 | 5、6、8、11 |
| 本单拿订单→扩写→政策→Agent，业务事实澄清 | 5、6、8 |
| 缺号卡片、原 session 恢复、重启/取消/重复点击 | 7、8、10、11 |
| 原因由用户自选、明确提交、MySQL 幂等申请 | 1、9、10、11 |
| Context7/版本核验、不换技术、阶段日志 | 全局约束、每任务、12 |
| 纯 Prompt/数据评估替代 TDD，前端 Vibe 例外 | 2～6、8、10；其余后端保持 TDD |
| 真实浏览器与演示/测试/dev-notes 完结交付 | 10～12 |

自查必须在提交前完成：接口名/DTO 单一来源、所有步骤有明确结果、无未经批准的方案切换、五个 Review Focus 有任务测试、纯数据与前端例外保留、计划评审状态未冒称通过。

本次自查结果：12 个任务覆盖全部 spec 要求；统一 DTO 来源、校准/正式测试 split、政策独立阈值、账本和申请幂等、旧分类评估调用适配均已补齐。检查时发现并修正一处完整问题测试误用“无可信订单”的断言；具体阶段仍是待用户评审，所有实施复选框保持未完成。

## 执行方式与评审

推荐 **Native**：由当前主代理按任务顺序实现，后端每任务 TDD/评估留痕，最后一次独立整分支后端 review。任务共享 State/DTO/模型预算/同一图的接口较多，顺序执行能减少分散上下文和重复评审成本。

也可选 **Subagent-driven**：按任务派实现子代理与独立审查；前端任务仍免 brainstorm/TDD/code review，不因执行方式改动用户例外。无论选哪种，均须用户先审阅本计划并明确执行方式，然后才进入实现技能。
