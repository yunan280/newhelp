# Ch06 冻结标注

这些是实现业务 Prompt 前由开发者人工编写并核对的演示场景，不是真实客服对话或经第三方标注的数据。订单事实固定到 2026-10-02；1001 未拆封机械键盘、1002 耳机拆封状态未知、1003 保温杯待发货。样例中的历史与额外用户事实是独立夹具，不当作线上事实写入目录。

正式集：意图 64 条（八类各 8）、理解 32 条、多轮 8 个独立 session × 4 轮、扩写/跳过 16 条、资格判断 12 条，共 156 个标注单位。独立校准集：意图 32 条、政策相关性 16 条，不进入正式分母。Prompt few-shot 不复制正式集。freeze.json 记录各文件 SHA256、条数与分割，标签冻结后不为通过评估改写。

完整问法检查 exact_question，消解问法检查必要片段、禁止事实、scope 和可信订单。多候选不允许自动选订单；新 session 不带旧实体。理解和扩写的自动检查无法代替语义审核，实际报告必须逐例人工阅读，并留下人工审核记录。模型自报 confidence 不能当成已校准的正确概率。

评估器重新比较 actual 与冻结 expected，不接受输入的 passed 标志；缺例、输出缺失、服务异常或 hash 不符均失败。原始 JSON 与格式纠正后的解析率分别报告，原始输出、模型、用量和耗时全部保存。只接受全新输出目录，失败尝试不能被覆盖。

```powershell
$pyCh06 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation freeze --dataset eval/ch06
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation calibrate --dataset eval/ch06 --outdir artifacts/ch06/<新的run-id>/calibration
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation run --dataset eval/ch06 --parts all --outdir artifacts/ch06/<新的run-id>/primary --mode primary --calibration <已验证校准文件>
```

实现与实际模型评估均已接入。模式配置、政策校准和真实 HTTP 命令见仓库 README 的 Ch06 节。默认 primary；cascade 须在同一既有上游显式设置 CH06_CASCADE_ENABLED=true、CH06_SMALL_MODEL=deepseek-flash，并另跑 calibration-cascade，不能复用 primary 的模型 hash。

历史失败完整保留：run-03 的余额402；run-03重试的省略指代；run-04虽标注集通过，真实物流回答后“这个能退吗”未承接；run-05修正Prompt后主模型156/156，cascade有1条旧历史污染引用。run-06中途再次402，primary93/156（63服务错误）、cascade121/156（34服务错误+1隐含退货防护误拒，后已控制回归修复），不能计为通过。标签文件和freeze.json未为通过结果改写。

最新用户明确要求不重复跑。run-07已启动的两次校准立即终止，没有作为通过报告；calibration-*-reused是对run-06已完成32条分类校准的离线复算，检查实际分类Prompt/请求及校准实现/模型hash/冻结输入未变，没有新模型调用，不改任何历史正式结果的hash。当前理解防护总体模型表现没有再次全量评估，不能以离线校准复用代替这一项验证。

标注集来自演示场景，不能代表真实客服总体准确率。尤其独立历史夹具并不等于真实前一轮模型回答；真实HTTP多轮和逐例语义人工审核必须另外保留。降级集若没有触发升级，不能把0次升级解释成低置信度升级已被真实模型覆盖；该分支由控制回归验证一次升级上限。
