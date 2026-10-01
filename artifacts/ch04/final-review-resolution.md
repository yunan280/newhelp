# Ch04 唯一最终修复 pass

评审范围：0e3f3e2..996a1d0；原始结论见 final-review.md。修复提交22274b2。
执行人Root：先验证7项Important实际影响，全部保留等级；不派二次评审。无Critical、无Minor。

| Important | 修复与验证 |
| --- | --- |
| 混合业务/知识绕证据门控 | business_only必须确认整轮纯业务，原话知识意图仍优先；JSON/SSE原始绕过复现先RED后GREEN |
| 单位/条件保护 | 数值或条件整句逐字保护；单位截断、条件丢失、否定修饰调换、1公里→1公斤同义词均RED→GREEN；真实原Query标注12/12 |
| 普通Markdown来源404 | document/table与policy/manual同样受路径containment限制；真实ingest生成块的原文读取RED→GREEN |
| FAQ超长上下文未入池 | 仅UnsupportedContextError转为缓存typed artifact，共用明确拒答/独立问题池；两次FAQ仅一次检索/入池，RED→GREEN |
| 迁移漏主键/自增 | 检查id单列主键及MySQL AUTO_INCREMENT、SQLite整数PK；真实缺PK和反射缺自增RED→GREEN，实际MySQL迁移两次通过 |
| 生成失败丢检索指标 | 正式runner先保存同一次检索结果/指标，再复用证据生成；模拟真实生成连接失败仍保留32有效GT分母，RED→GREEN |
| 首次安装缺rag | 首次安装修正为.[rag,dev]；uv真实dry-run解析89项依赖，实际应用导入通过 |

代码回归：17条新回归（首16条实际RED，中文未知单位补例另见RED），Query局部36通过；全套536 passed / 5 deselected，Ruff通过。纯Prompt/数据按用户例外使用真实标注验证：Query12/12，负面承诺3/3。

修复后真实run03四策略160次全部完成，服务/judge错误0；从逐题数据重算全部四策略指标/有效分母与summary一致，current Prompt及原冻结语料/GT hash全部一致。线上MySQL JSON/SSE4/4、隔离型号/来源/原生BM254/4、额外负面知识/缺参5/5、混合业务知识JSON/SSE2/2实际通过，所有拒答记录均在对应SQL独立提交。证据位于ch04_20260930_03。校准路径切换至run03，生产知识集合不变。

前端未修改，先前真实手动确认及Vibe协议样例验收仍有效；无二次代码评审，executor以RED/GREEN、全套回归和真实验收确认7项全部解决，无待修复Important、无Minor。保留正式BM25/混合各一次误放和每策略一次误拒，示例数据不证明生产泛化提升。
