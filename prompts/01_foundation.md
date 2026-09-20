# 阶段1：建立可运行的独立骨架

请先读取AGENTS.md、PROJECT_SPEC.md与docs/PROGRESS.md，只实施本阶段。

1. 确认新项目根目录、已有文件与Python版本。选择兼容依赖并记录版本，不影响其他虚拟环境和焊接Agent。建立app.py、src/cf_stitch/{domain,services,knowledge,models,optimization,storage,providers}、tests、data、artifacts等合理模块，不创建大量无功能假页面。
2. 使用Streamlit建立中文界面，含任务工作台、资料参数、实验设计、数据管理和设置的最小可用入口；模型与优化区域明确标注尚未训练，不展示假预测。
3. 定义Pydantic领域对象：材料/构型、线迹机制、路径图案、平台、工艺参数、设备来源声明、人工确认的设备配置、目标与结果。机器人属于平台，人字形属于路径，无底线自锁保留自定义未确认。为每类数值保留单位、测量阶段、设定/实测区分和来源。
4. 导入spec中的种子为只读来源声明，不自动提升为硬限或实测。建立SQLite存储和有版本的迁移方法。
5. 提供requirements或pyproject、.env.example、.gitignore、README和Windows start.bat。默认本地8502；检测端口占用并报告，不杀其他进程。不把密钥、源报告、模型和实验原始数据提交Git。
6. 无API、无真实数据也能启动和看参数。真实数据表为空，演示模式明确且隔离。

验收：实际运行导入和schema单元测试；能启动应用；页面明确零真实实验/零已训练模型；机器人不能误填为线迹；非法负针距被拒绝；未缝合组p/s可为空。更新进度，给出准确启动命令与尚未测试的浏览器行为，不提前实施阶段2。
