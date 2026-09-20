# 总口令：先设定项目边界

请为我独立开发“CF-Stitch Agent——碳纤维预制体缝合工艺研发助手”。这是新项目，不能覆盖或改动已有铝合金焊接 Agent。

先确认当前工作目录和未提交改动，只在我选定的新项目根目录开发。阅读根目录 AGENTS.md、PROJECT_SPEC.md、docs/SOURCE_AUDIT.md、spec/domain_parameters.yaml、spec/experiment_templates.yaml 和 sources/manifest.json。没有原始DOCX时可以使用 sources/extracted/，但要声明没有读取工程图；缺失的文件不能假称已读取。

第二份参数文档是研究参数与试验设计基础；第一份报告是 V形拼接、J型梁、双层叠层固定的工程背景。做“证据检索+参数规则+几何计算+数据管理+数值预测+约束优化+下一轮实验”的闭环，不做仅能聊天的网页。当前没有真实训练数据，不能制造强度、韧性、合格率和模型成绩。数据后续由我提供。

推荐 Python、Streamlit、SQLite、Pydantic、scikit-learn 的本地模块化实现；先检查本机环境，使用独立虚拟环境。默认127.0.0.1:8502，不公开部署，不改系统代理，不占用焊接项目。LLM联网接入可选，基础功能无API也要工作；模型负责数值，LLM只取证、调用工具和解释。首版禁止真实PLC写入与机器人运动。

按prompts中的阶段1至7逐段执行。现在只确认文件、项目边界和阶段计划，整理未决问题，不开始所有阶段。后续每阶段实际改代码、跑测试、更新docs/PROGRESS.md；报告已完成与未完成后停止，等待下一条口令。
