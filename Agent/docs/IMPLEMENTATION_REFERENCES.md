# 软件实现参考（与材料资料分开）

这些参考仅支持软件实现，不用于修改D1/D2的材料参数。以下页面于本次方案准备时核查；Codex实施时应再次以实际安装版本文档为准。

- OpenAI：Custom instructions with AGENTS.md。项目长期约束放在AGENTS.md；约束仍需后端校验与测试落实。
- OpenAI：Function calling。模型提出工具调用，应用执行并返回结果；参数约束不能代替实际执行权限控制。
- scikit-learn：GroupKFold / Cross-validation。非重叠分组验证，独立组数限制可用折数。
- scikit-learn：Common pitfalls and recommended practices。训练/测试隔离与折内预处理，避免数据泄漏。
- scikit-learn：GaussianProcessRegressor。支持均值及标准差/协方差预测；本方案额外要求报告适用域和校准状态，不能把模型标准差误当工程合格概率。

对应官方页面：

```text
https://developers.openai.com/codex/guides/agents-md
https://platform.openai.com/docs/guides/function-calling
https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html
https://scikit-learn.org/stable/common_pitfalls.html
https://scikit-learn.org/stable/modules/generated/sklearn.gaussian_process.GaussianProcessRegressor.html
```
