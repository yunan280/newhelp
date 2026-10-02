# Ch06 冻结标注

这些是实现业务 Prompt 前由开发者人工编写并核对的演示场景，不是真实客服对话或经第三方标注的数据。订单事实固定到 2026-10-02；1001 未拆封机械键盘、1002 耳机拆封状态未知、1003 保温杯待发货。样例中的历史与额外用户事实是独立夹具，不当作线上事实写入目录。

正式集：意图 64 条（八类各 8）、理解 32 条、多轮 8 个独立 session × 4 轮、扩写/跳过 16 条、资格判断 12 条，共 156 个标注单位。独立校准集：意图 32 条、政策相关性 16 条，不进入正式分母。Prompt few-shot 不复制正式集。freeze.json 记录各文件 SHA256、条数与分割，标签冻结后不为通过评估改写。

完整问法检查 exact_question，消解问法检查必要片段、禁止事实、scope 和可信订单。多候选不允许自动选订单；新 session 不带旧实体。理解和扩写的自动检查无法代替语义审核，实际报告必须逐例人工阅读，并留下人工审核记录。模型自报 confidence 不能当成已校准的正确概率。

评估器重新比较 actual 与冻结 expected，不接受输入的 passed 标志；缺例、输出缺失、服务异常或 hash 不符均失败。原始 JSON 与格式纠正后的解析率分别报告，原始输出、模型、用量和耗时全部保存。只接受全新输出目录，失败尝试不能被覆盖。

```powershell
$pyCh06 = (Resolve-Path .venv-ch03/Scripts/python.exe).Path
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation freeze --dataset eval/ch06
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation calibrate --dataset eval/ch06 --outdir artifacts/ch06/<新的run-id>/calibration
& $pyCh06 -X utf8 -m mewhelp.ch06.evaluation run --dataset eval/ch06 --outdir artifacts/ch06/<新的run-id>/primary --mode primary --calibration <已验证校准文件>
```

这些是待实现模块齐备后执行的命令；标签冻结与评估器单测通过不表示业务 Prompt 已通过真实模型评估。浏览器和真实数据库验收另外记录，不能用本文件夹的夹具结果冒充。
