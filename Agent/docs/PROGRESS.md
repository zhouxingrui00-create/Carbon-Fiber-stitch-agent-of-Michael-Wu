# 开发进度

## 当前状态

**阶段 4 已完成并实际测试（2026-09-20）。停止在阶段 4，阶段 5–7 未实施。**

**当前项目根目录：`C:\Users\22846\Desktop\碳纤维复合材料缝合技术\Agent`。** 用户已要求把开发项目与交接包分开；下文阶段 1/2 的旧路径是迁移前的历史记录，不能再作为启动目录。

现在可无 API、无真实实验数据启动，使用阶段1–3的来源检索、参数/场景审查与计算，并保存七组待实验计划、下载空白模板、预览映射与事务导入CSV/XLSX、查看实验关联与来源统计。没有搜集真实数据、生成训练标签、训练模型、数值预测、优化候选或真实设备连接。

## 阶段 4 补充：日常简化模板页面入口（2026-09-20）

按用户要求，将此前仅交付在 `outputs/daily-template-20260920/CF-Stitch_日常简化记录表.xlsx` 的文件加入正式模板目录 `templates/stage4/daily_measurements.xlsx`。两个文件 SHA-256 均为 `ac01fbd8e15756e66d42d5cf0851dbda337773d33845a4a2e7486115957ef41f`，内容相同：26列中文表头、0条测量、两个工作表“日常记录”和“填写说明”。未修改原完整模板。

- 修改 `src/cf_stitch/experiments_ui.py`：在 **数据管理 → 导入预览 → 导入字段说明与空白模板** 增加“下载日常简化版 Excel（26列中文）”；实验设计页共用下载区也显示同一入口。补充简化版与58列完整版的适用说明、数据表选择及手工字段映射提示。没有改变导入校验、默认值或事务行为。
- 新增 `templates/stage4/daily_measurements.xlsx`；更新模板 `README.md` 及本进度。正式下载读取模板目录，不依赖 `outputs/`。记录缺失仍为空，未生成实验结果、训练数据或设备能力。
- 再次确认独立项目根目录；项目不是Git仓库，不能用Git列出未提交改动。未修改交接包、原始DOCX、焊接项目、系统代理或默认数据库；没有新增依赖，仍停止在阶段4。

实际测试：

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q tests/test_experiments_app.py --basetemp artifacts/pytest-daily-download-20260920 --junitxml=artifacts/daily-download-ui.xml
& '.\.venv\Scripts\python.exe' -X utf8 artifacts/daily-download-20260920/verify.py
```

已有阶段4界面回归 **10 passed in 7.01s**。定向检查通过：目标折叠区及实验设计页均生成简化版下载按钮，实际传入的文件字节/文件名/MIME正确，原四个下载仍保留；应用解析器回读正式XLSX为26列0记录，工作表及文件哈希匹配。全部AppTest使用隔离数据库。检查报告见 `artifacts/daily-download-20260920/verification.json`。

本轮浏览器控制连接返回 `nodeRepl.fetch request failed`，没有实际完成浏览器点击下载及保存文件，也未进行原生Excel/WPS编辑验证；上述AppTest不等同于浏览器端到端验收。启动方式仍为Agent内 `start.bat` 或 `scripts/launch.py`，默认 `127.0.0.1:8502`；已运行页面刷新后进入上述位置查看新入口。本轮未重启或停止任何用户服务。

## 阶段 4 边界与实现（2026-09-20）

开始时已核对独立 `Agent` 根目录并读取 AGENTS、PROJECT_SPEC、SOURCE_AUDIT、进度、两份 spec 和 `prompts/04_data_experiments.md`。项目及父目录不是Git仓库，`git status --short` 返回 `not a git repository`，不能声称无未提交改动；未初始化Git、提交、推送或公开部署。只修改Agent项目，未改同级交接包、原始DOCX、焊接项目或系统代理。本阶段不联网搜集真实数据、不安装新依赖。

1. **关联实体与测量schema**：分别保存来源、材料/批次/卷号、父预制体、设备标识、实验计划、运行及设定、试样及来源/运行段、测量、共享对照、原始导入文件、导入批次/映射/源行。设备标识不是人工确认能力。计划关联验证原组名及已填写的机制/p/s，缺值保留null；相同运行的不同试样段可分别记录，仍进入同一依赖组。
2. **数值与来源隔离**：58个导入字段保留成型/后处理、测试方法、加载方向、失效模式、几何、测量阶段、不确定度及方法。设定和观测独立，空值要求原因，不用0补齐；拒绝布尔伪数、NaN/Infinity与未知结果单位。率值须保留整数分子、正整数分母和观察窗口，单位为1或%，并核对与计数的一致性。实验实测、文献实测、仿真、demo、prediction分开；demo仅进入demo空间，有值记录与待测空记录分开统计。旧v1记录来源不明时不冒充实测标签。
3. **CSV/Excel流程**：显式CSV编码（UTF-8/GB18030）、XLSX工作表选择、原始预览、逐列映射、行号及问题清单、规范化预览、确认后提交。非空未知列须显式忽略，不能静默丢列；不推断单位/日期格式。工作表清单独立读取，首张说明页或空表不妨碍选择后续数据表。支持静态.xlsx，拒绝.xls/.xlsm、公式（含缓存）、宏、合并、外链、XML实体和越界ZIP；单次20MiB、2万数据行、256列。
4. **不可变原件、版本与事务**：保存上传原始字节与SHA-256，以及文件名、编码、工作表、原行号、映射、解析/规范化版本、内容指纹和测量关联。数据表阻止UPDATE/DELETE；提交重新读取原件、验证预览及当前库，使用单事务写入全部关联，失败整体回滚。相同导入/测量幂等；同ID不同内容拒绝，换ID不能扩增同语义测量。文件/映射/空间变化使旧预览失效。清洗版本以导入元数据记录，尚无覆盖旧数据或交互更正版本流程。
5. **对照与依赖**：已知材料上下文冲突或同指标的方法/方向/单位/测量阶段不匹配会阻断，未知条件提示尚不可证明可比。共享父预制体、运行、试样、对照、同卷号及文献来源形成依赖组件；卷号跨来源保守连接，可能过度分组，需研究者后续核对。文献汇总均值不得附个体试样或复制成独立重复；报告样本数仅元数据。相同文献不同工艺条件即使均值恰好相同也可分别保留，但仍同文献依赖组。所有组当前 `training_eligible=false`。
6. **七组原样计划**：C0未缝合，p/s为空；L1/L2/L3锁式与C1/C2/C3链式，分别5×5、5×10、10×10mm。重新核对D2:t006原文、引用哈希、来源快照和完整条件，不接受被改写的来源或设计建议。保留约20–25mm背景与材料/纱线/铺层/压实/成型/后处理一致及匹配C0要求。重复数、独立制样数、针径、张力、针频/速度、测试方法/方向、随机种子和区组依据均为null，所有measurements为空。先低速针线筛查，再固定其余条件研究机制与p/s；不默认填入低速数值。
7. **计划审查与中文界面**：保存计划只写计划表，不创建七个试样或实验。选定并声明适用的人工厚度限制后，6mm上限对20–25mm六个缝合组的两个端点均BLOCK；缺设备/机构为UNKNOWN，已确认机制不支持锁式或链式时BLOCK，始终不可执行。界面增加计划保存/审查与模板下载，数据页增加来源统计、预览导入、关联实体和依赖组。没有模型成绩、伪预测或批准上机按钮。

## 阶段 4 实际测试

完整回归命令：

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q --basetemp artifacts/pytest-stage4-full-20260920 --junitxml=artifacts/stage4-tests.xml
```

**561 passed in 43.79s，0 failed，0 skipped。** 保留333项阶段1–3回归，新增228项如下：

| 检查组 | 数量 | 覆盖 |
|---|---:|---|
| 实验schema | 44 | null/真实零、阶段/单位、分子分母、机制/路径/平台、文献均值、demo隔离 |
| CSV/XLSX导入 | 61 | 编码/映射/原行/空值、选择非首表、安全拒绝、字面文本不执行、预览篡改、真实SQLite回滚与原bytes保存 |
| 七组计划 | 75 | 原表/来源/适用条件、所有待确认null、空结果、6mm厚度阻断、针深贯穿语义、机构不匹配、来源篡改拒绝、空白文件回读 |
| 实验存储 | 38 | v1/v2→v3保留旧数据、幂等/冲突、文献伪重复、对照关系、跨来源同卷依赖、运行段、计划关联、只读与事务故障回滚 |
| 阶段4 AppTest | 10 | 保存计划/重开、导入预览与确认、null、整批失败、文件/映射失效、demo拒绝/隔离、空模板、多表选择和明确选择6mm限制后阻断 |

最终修正界面字段名的Markdown显示和上传控件20MiB限制后，又实际运行 `tests/test_experiments_app.py`：**10 passed in 6.98s**，见 `artifacts/stage4-ui-final.xml`。这些测试不是新增10项，不把总数报为571。`pip check` 返回 `No broken requirements found.`。完整套件包含15项启动测试：真实Streamlit HTTP首页/健康检查、中文表格往返、专属进程退出和端口释放。

开发中实际发现并修复：首张非数据表挡住工作表选择、计划保存未充分重核来源、同一运行不同试样段误报冲突、不同工艺组相同文献均值误报重复、卷号跨来源依赖漏连接。新增设备界面测试初次因夹具未索引引用失败，补齐测试前置来源索引后通过，没有绕过来源校验。默认库验收脚本初次使用错误路径属性，在迁移已完成后停止，修正脚本并重开v3核对成功；未还原或重建用户库。

## 阶段 4 默认库与文件保护

迁移前已生成 `artifacts/stage4-before-v3.sqlite3` 备份及 `stage4-before-v3.json`。最终默认库为SQLite v3，旧业务表逐表内容哈希与记录数均保持，迁移记录为1/2/3；`integrity_check=ok`，`foreign_key_check=[]`。原2份spec、manifest、2份抽取JSON、2份DOCX及交接包Markdown逐一哈希未变。D1原件/清单身份差异和未解析图片结论不变，未声称解决。

默认库仍为2个资料来源、33条参数声明、4个证据快照和973个证据块；真实与演示任务/人工确认/训练模型仍为0。新增实验来源、材料、父预制体、运行、试样、对照、测量、导入和已保存计划也均为0。七组待实验计划已输出文件；需用户在界面命名保存，未把模板自动塞入用户实验库。验收记录只在 `artifacts/` 隔离库中，详情见 `artifacts/stage4-readiness.json` 与 `stage4-observation.schema.json`。

## 阶段 4 浏览器、交付与停止点

实际在Codex内置浏览器、临时本地端口55899及隔离库 `artifacts/stage4-browser-20260920/data/` 检查：

- 七组表格、20–25mm及空结果提示；填写计划名并保存成功，显示来源/缺项、UNKNOWN与不可执行；数据统计只增加1个计划，运行/试样/测量仍为0。
- 通过真实文件选择器上传本地 `demo-null.csv`（明确软件验收、结果为空）；显示哈希/原始行/映射。研究空间拒绝，切换demo后旧预览清除并允许重新预览。
- 确认前提交按钮禁用；确认并提交后新增1条demo空值记录；再次预览识别重复，第二次提交inserted=0、duplicates=1。实际库复核value为null、真实测量0、demo有值标签0，模型/优化保持not_ready。
- 临时页已关闭，专属stdin通道正常停止验收进程（退出码0），端口55899已释放；没有停止其他服务。

尚未进行的浏览器交互：Excel原生文件选择/上传全流程、下载按钮文件保存、浏览器中人工设备限制选择、所有关联实体逐项操作、Chrome/Edge、多用户并发、屏幕阅读器及完整移动端适配。Excel多表选择与设备限制通过AppTest/后端测试，不能冒充浏览器覆盖；没有原生Microsoft Excel打开编辑验证。内置浏览器窄屏下已使用侧栏展开/收起，未做完整响应式验收。最后两项界面文字/上传限制调整由AppTest复测，未重新上传浏览器文件。

交付文件：`templates/stage4/seven_group_plan.json`、`seven_group_plan.csv`、`blank_measurements.csv`、`blank_measurements.xlsx` 和说明README。Excel以artifact-tool制作，数据页58列0记录，说明页逐字段中文；已检查、渲染查看两页、用应用解析器回读CSV和Excel。计划清单不能当测量导入，文件内无实验结果。

新增/修改：

- 新增 `src/cf_stitch/experiments/{__init__,models,importing,plans}.py`、`storage/experiments.py`、`experiments_ui.py`；修改 `storage/database.py` 与 `ui.py`。
- 新增 `tests/test_experiment_models.py`、`test_experiment_storage.py`、`test_experiment_importing.py`、`test_experiment_plans.py`、`test_experiments_app.py`；修改旧 `test_app.py`、`test_storage.py`、`test_evidence_storage.py` 的页面/版本断言。
- 新增上述5个模板文件；更新README、AGENTS、PROGRESS、pyproject说明和requirements注释，依赖版本及锁定文件未变。
- 忽略目录 `artifacts/` 内的备份、测试报告、模板制作脚本/渲染、浏览器验收脚本与readiness报告是本地软件验证产物，不是实验数据集。

启动仍为双击Agent内 `start.bat`，或用本项目Python运行 `scripts/launch.py`。默认保持 `127.0.0.1:8502`；最终检查发现8502已占用，未停止占用程序。仅在验收命令进程临时设置 `CF_STITCH_PORT=8503` 后启动检查通过，未修改默认配置或系统环境。README给出临时换端口命令；当前没有保留本轮验收服务。

已知边界：来源真实性依赖用户声明；外部原始文件引用未自动读取核验；不做自动单位猜测/数据修补、完整设备审批、已保存计划编辑或原记录更正。新关联实体仍通过测量/待测记录导入建立，未提供独立实体编辑器。依赖组用于后续防泄漏验证，不代表有效独立样本数。阶段5–7与真实数据收集均未开展，按用户要求停止。

---

以下为阶段3及更早历史记录，“当前”“未实施”表述以本页顶部阶段4状态为准。

## 阶段 3 边界与实现（2026-09-20）

- 开始时确认当前开发根目录为 `Agent`；项目与上层目录均不是 Git 仓库，`git status --short` 返回 `not a git repository`，不能声称 Git 无未提交改动。没有初始化、提交或推送 Git。
- 已读取 AGENTS、PROJECT_SPEC、SOURCE_AUDIT、两份 spec、进度和 `prompts/03_rules_scenarios.md`。仅修改独立 Agent，不改同级交接包、原始 DOCX、焊接项目或系统设置。没有引入网络服务、API 或新依赖。
- SQLite 继续为 v2，本阶段不迁移、不清空或修改默认用户库。规则结果只读展示，不改任务状态，不把计算值写成实测或训练数据。

已实现：

1. **独立规则结果**：`RuleResult` 返回 PASS / WARN / BLOCK / UNKNOWN、字段、原因、来源引用、规则依据、适用假设与原输入；阻断/未知不能包含可用计算数值。`ReviewReport` 综合优先级为 BLOCK > UNKNOWN > WARN > PASS，候选仍是草案或被阻断，可执行始终为 false。
2. **参数与来源窗口审查**：检查独立物理字段、规范单位、有限数、正负约束和未缝合空值。文档窗口外只警告，不直接判断数学非法或设备危险；薄/厚件示例不构成5–10 mm禁区。D2通用供纱与细纱窗口须明确选择；研发起步10–100针/min须确认起步条件。针径窗口不替代针线匹配审查。工程场景使用各自窗口，不自动裁进D2针距、行距或针频范围。
3. **人工设备适配**：仅使用明确选用且确认适用的人工限制，逐字段比较；超限返回 BLOCK。针距/行距/机械间距，供纱/两层布面张力，针频/线速度，厚度/针深/抬脚，重复定位精度/最终线迹误差互不替代。多条同字段确认范围不自动择一或取交集。来源声明不能冒充人工设备配置。
4. **确定性计算**：同量纲单位换算保留原输入；Hz与针频要求每周期针数。矩形阵列密度要求规则矩形、每格一个穿刺点，并排除回针、双针、额外锁固及有限边界影响。未缝合p/s为空，专门分支返回理论0，不执行除法。复杂/未确认机构或路径返回不支持/未知。理想直线送料速度要求实际每针送料量及同步/周期假设。匹配对照提升率/损失率缺条件、缺均值或零分母不出结果；负损失不截断。全部标为公式结果，非实测或预测。
5. **独立三场景配置**：V形沿缝p=3–10 mm与摆动5–15 mm、6 mm深度、800针/min等声明分开；J型保留5–15针/cm、30 mm/10 Hz声明、工位1/2固定头与3机器人，机构仍custom_unconfirmed；双层保留两层各20–80 N、p=10–30 mm、100–500针/min、错位目标0.5 mm。5/6 mm、链式/双线自锁、100/120/200 mm机械间距和1200/1500–2000 mm空间描述均并列待确认。
6. **来源卡与几何边界**：原种子字段/引用不变；补充的幅宽、J机头尺寸/质量/功率/工位、双层空间和机构原句，先验证提取版本的原文锚点，再作为 source_only/advisory 的配置声明展示，不写回种子或默认库。J针密只有勾选均匀单排假设才展示约0.667–2 mm，仍WARN/pending、不纳入优化。没有CAD/机构/TCP/夹具或运动学求解器时不认证可达性/避碰；即使输入资料齐全，本阶段也不会给出已验证结论。
7. **中文界面**：新增“三场景与审查”，含场景参数卡、条件化计算、已存任务审查。任务表单增加可空厚度及条件、机械间距、针深、抬脚、上下布面张力和线速度。可单选已有人工确认记录用于只读审查，须勾选本次适用；不默认合并记录、不存整机配置或提供上机批准按钮。场景特有几何信息目前以未设定参数卡和缺项清单展示，不是CAD编辑器或完整场景上下文录入流程。

## 阶段 3 实际测试与验收

```powershell
Set-Location 'C:\Users\22846\Desktop\碳纤维复合材料缝合技术\Agent'
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q --basetemp artifacts/pytest-stage3-full-20260920 --junitxml=artifacts/stage3-tests.xml
```

**333 passed in 24.64s，0 failed，0 skipped。** 旧177项回归及新增156项均通过；新增为计算80项、参数/设备规则43项、场景21项、AppTest交互12项。包含实际DOCX读取、schema、SQLite隔离、来源/注入、Windows启动、真实HTTP首页与健康检查、服务退出释放端口。测试中的设备和均值均为隔离软件夹具，不是实验结果。

| 验收项 | 实际结果 |
|---|---|
| p×s=5×5 / 5×10 / 10×10 / 10×20 mm，明确矩形假设 | 40000 / 20000 / 10000 / 5000 点/m² |
| 5 mm × 100针/min，明确直线同步送料、实际送料量、每周期一针 | 0.5 m/min |
| 10 Hz，明确每周期一针 | 600针/min；未确认周期关系则UNKNOWN且无值 |
| 人工确认稳定缝合厚度或适用总厚度上限6 mm，试件22 mm | BLOCK，candidate_state=blocked，executable=false |
| 仅人工确认针刺深度6 mm，试件22 mm | 未明确贯穿要求不冒充厚度能力；明确贯穿后BLOCK |
| 双层固定p=20 mm | 当前场景窗口PASS，不套D2通用3–15 mm阻断 |
| 未缝合、零/负针距、复杂路径、非有限数、数值溢出/下溢 | 未缝合空值分支理论0；其他不满足条件不返回可用计算数值 |
| 对照未匹配或为0 | 不输出提升率/损失率；匹配夹具12/10的损失率保留-20% |
| J型5–15针/cm | 无假设UNKNOWN；均匀单排假设后WARN并保持pending |
| 来源缺失、畸形边界或文档注入文本 | UNKNOWN或明确报错，不执行文本、不制造设备硬限 |

独立复查实际发现并修复：D2-F的`research_startup`适用范围被漏选、J型原始Hz声明未提示周期关系、工程场景显式供纱条件未生效、畸形来源列表异常逃出。补充相应回归后执行了上述完整测试，没有用UNKNOWN总状态掩盖字段级遗漏。

`pip check` 返回 `No broken requirements found.`；`scripts/launch.py --check` 实际通过，默认仍为 **127.0.0.1:8502**。没有安装依赖或修改代理。完整哈希核对见 `artifacts/stage3-protected-before.json` 与 `stage3-protected-after.json`：两份原始DOCX、两份spec、manifest、两份提取JSON及默认SQLite在本轮验收前后均未变化。

## 阶段 3 浏览器抽查与未测项

使用 Codex 内置浏览器、临时回环端口58991及隔离数据库 `artifacts/stage3-browser-20260920/data/` 实际检查：

- 中文阶段3工作台、新入口和新增厚度/设备适配输入折叠区可见。
- 场景卡从通用研发切换到双层固定，实际显示独立10–30 mm/100–500针/min、两层20–80 N，以及5/6 mm、机构、机械间距和空间待确认提示。
- 填写锁式/平行路径、p=s=5；未确认假设时显示UNKNOWN且无数值。勾选矩形、每格一个穿刺点、无额外穿刺后，显示PASS及40000 points/m²，并明确不是有限试件针数或实测质量。实际截图检查表单与结果区布局。
- 本轮没有通过浏览器创建测试任务或人工设备记录；默认用户数据库未写入测试数据。

**尚未实际浏览器覆盖**：人工限制选择并勾选适用后22 mm阻断的完整流程、新增任务全部字段的提交/刷新、单位/速度/J针密/匹配对照各计算入口完整交互、场景卡所有原文JSON展开与表格下载/列菜单；这些主要由AppTest及独立函数测试覆盖。Chrome/Edge独立浏览器、完整移动端/屏幕阅读器、多用户并发仍未测试。不将AppTest或HTTP检查称为完整浏览器验收。

临时浏览器标签已关闭；本次专属服务通过stdin停止，返回码0，58991端口已释放，见 `artifacts/stage3-browser-20260920/shutdown.json`。没有保持测试服务运行或停止其他程序。

## 阶段 3 文件清单、启动与停止点

新增程序：

- `src/cf_stitch/rules/__init__.py`、`results.py`、`calculations.py`、`validation.py`
- `src/cf_stitch/scenarios/__init__.py`、`base.py`、`v_splice.py`、`j_beam.py`、`dual_layer.py`
- `src/cf_stitch/rules_ui.py`

新增测试：`tests/test_calculations.py`、`test_rules_validation.py`、`test_scenarios.py`、`test_rules_app.py`。

修改：`src/cf_stitch/ui.py`、`README.md`、`AGENTS.md`、`pyproject.toml`、`requirements.txt`（仅阶段注释）、`docs/SOURCE_AUDIT.md`、`docs/PROGRESS.md`。本地验收脚本、JUnit XML、隔离数据库、哈希清单与浏览器日志位于被忽略的 `artifacts/`，不是实验数据。

启动：双击 `Agent/start.bat`，或在Agent根目录执行 `& '.\.venv\Scripts\python.exe' -X utf8 scripts/launch.py`，访问 `http://127.0.0.1:8502`。若已有旧服务进程，需自行结束该旧服务再从本项目入口重启；程序不会杀其他端口占用者。

**停止在阶段3。** 不进入阶段4实验记录/设计生成、阶段5模型训练、阶段6优化或阶段7LLM编排。没有通用新增DOCX导入、有限试件真实针位计数、机器人轨迹、CAD碰撞验证或真实设备控制。本轮只实现确定性研究工具，不声称材料性能或设备能力经过实验验证。

以下保留阶段1/2和目录迁移的历史记录，当前能力以本阶段记录为准。

## 目录分离与迁移验收（2026-09-19）

按用户要求完成目录整理，没有开发阶段 3 功能：

- 迁移前检查目标 `Agent` 为空，源目录与目标均不是 Git 仓库。所有实际移动前检查绝对路径位于本工作目录内，没有覆盖目标已有文件或批量删除。
- 程序入口、`src`、`scripts`、`tests`、`.streamlit`、依赖文件、启动脚本、数据库、历史测试产物及缓存已移至 `Agent`。
- 同级 `CF-Stitch Agent 开发交接包` 只保留规范、口令、来源资料和交接文档；新 README 指向实际项目。其进度文件保留为分离前阶段 2 快照。Agent 中复制了运行与继续开发所需的规范、参数、提取来源和口令，应用不读取交接包目录。
- 更新 Agent 的 `README.md` 与 `AGENTS.md`，明确新根目录及后续开发边界。原始两份 DOCX 仍只读保留在同级 `碳纤维`，查找规则在同级迁移后仍有效。
- 数据库移动前后 SHA-256 完全一致：`2B10B2AA0AB9E11F8B737B3CD563916BBE1C26256C94F092BE55026F2AEA9A4C`。随后在新位置加载资料，保留所有旧快照，只为 D1 提取 JSON 的新采集位置追加 1 个快照；现为 4 个快照、973 个证据块。不是新增实验资料或新参数；33 条来源声明、任务、测量、确认及模型计数均保持不变，数据库完整性检查为 `ok`。
- 在 `Agent/.venv` 重新创建 Python 3.12.14 环境并按原 `requirements-lock.txt` 安装；逐项核对全部 47 个锁定依赖版本一致、`include-system-site-packages=false`。未安装全局包或改系统代理。旧环境保存在 `artifacts/relocation-20260919/venv-before-relocation/`，仅作迁移备份，不作为运行环境。

实际验证：

```powershell
Set-Location 'C:\Users\22846\Desktop\碳纤维复合材料缝合技术\Agent'
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q --basetemp artifacts/pytest-relocation-20260919 --junitxml=artifacts/relocation-20260919/tests.xml
```

**177 passed in 19.48s，0 failed，0 skipped。** 包括领域与来源 schema、两个实际 DOCX、AppTest、Windows 启动脚本、新环境表格依赖、真实 HTTP 健康/首页和测试进程退出/端口释放。`pip check` 通过；新目录的 `pip.exe` 与 `streamlit.exe --version` 可正常执行。本次未重新做真实浏览器交互，阶段 2 浏览器记录保持为历史验收，不把 AppTest 当浏览器测试。

默认 8502 仍被占用，启动检查按设计报错，没有停止占用程序；仅在检查进程临时设置 8503 后检查通过，项目默认仍为 `127.0.0.1:8502`。需要暂用备用端口时，可在当前 PowerShell 执行：

```powershell
$env:CF_STITCH_PORT = '8503'
& '.\.venv\Scripts\python.exe' -X utf8 scripts/launch.py
```

迁移清单、数据库哈希及验证报告在 `artifacts/relocation-20260919/`；没有保持测试服务运行。之后双击 `Agent/start.bat` 或在 Agent 根目录运行启动器。交接包不再是运行目录。

## 阶段 2 边界与交付

- 重新阅读 AGENTS、PROJECT_SPEC、SOURCE_AUDIT、两份 spec、manifest、进度与 `prompts/02_evidence_parameters.md`。根目录仍为本独立交接包；再次确认本目录及父目录不是 Git 仓库，不能声明不存在未提交改动，未初始化/提交/推送。
- DOCX/清单/原提取 JSON/种子只读保留。所有程序、数据库及验收产物都在本项目；没有改动焊接 Agent、全局 Python、系统代理或公开部署。
- 本轮按用户资料实现，不添加外部检索材料为工艺依据，没有上传私有资料。解析器使用标准库 ZIP/XML；没有安装新依赖、下载 embedding 或接入 API。

实际实现：

1. **结构化读取**：原件优先 `sources/originals/`，再查上一级 `碳纤维/`，兼容清单 `(2)` 文件名和本地去后缀名。无原件才读提取 JSON；存在但损坏的原件显式报错。保留 SHA-256、原文、原单位所在句、章节与识别依据、段落计数、表/行/单元格位置。外部关系不读取，宏/域/文档命令不执行。
2. **可追溯离线检索**：SQLite 参数化字面检索，空格分词为 AND；索引规范化与原文分开。按当前成功读取快照查询，历史快照可以显式选定后精确定位。空查询/无证据返回空，不造出处。
3. **参数词典**：原样保留 33 条种子的数值、单位、场景和条件。D2 p=3–15 mm、s=5–20 mm、供纱 0.5–10 N、细纱 0.1–3 N、针径 0.6–2.0 mm、起步针频 10–100 针/min 完整保留。薄件 1–5 / 厚件 10–30 mm 是示例，不将 5–10 mm 判为非法。针距 p、行距 s、机械间距以及供纱/布面张力分字段检索。
4. **证据性质隔离**：初始窗口、设计规格、质量目标、人工确认限制、实测、公式结果、预测均有独立分类。待测项目不当成实测。种子已有 J 针密条件推导和 Hz 单位换算额外标记 derived、假设未确认、非实测，未新增阶段 3 计算引擎。
5. **六项待确认**：J 型梁 5–15 针/cm、双层 5/6 mm、链式/双线自锁、机械多针 100/120/200 mm、摆动跨距定义、无底线自锁机构。并列原句及原始哈希，均为 pending，没有择一、覆盖、合并或取交集。
6. **人工确认独立存档**：来源仍只读；单项人工确认需设备名/版本、适用范围、有限边界、审核人、理由、带时区时间和可定位引用。保存不建立整机配置，不解除未决项，不批准上机。本地署名尚无账户认证。
7. **SQLite v2**：在事务内从 v1 升级；来源版本、证据块和人工确认表分开，均追加并禁止更新/删除。同一快照幂等、不同哈希/采集位置保留独立快照，明确版本可以回看旧原句。没有清空旧库。
8. **缺资料降级**：缺少种子/来源文件时保留已有任务和声明，提示核查；新空库仍可建立草案。当前证据搜索不使用历史快照冒充本次读取，模板缺失也不生成假方案。

## 阶段 2 来源与默认库实际结果

| 项目 | 实际读取结果 |
|---|---|
| D1 实际原件 | 356 段（含空段）、0 张正文表、46 个绘图对象；实际 hash `7ad8d0cd…61b51a9` 与清单 `f97ccd33…f04853b` 不同 |
| D2 实际原件 | 60 段、6 张表；含独立表格行共 107 块；hash `b4e2b137…3701c3` 与清单一致 |
| 旧提取内容逐块比对 | D1 的 255 个已记录非空段落及 D2 已记录段落/表格文字精确一致；不证明图片或整体文件同版 |
| 默认 SQLite | v1 → v2，33 条参数未变、33 条参数引用可定位；3 个来源快照、718 个证据块、6 项待确认 |
| 默认真实与 demo 空间 | 任务、实验、测量、整机确认、已训练模型在迁移前后均为 0；单项人工确认也为 0 |

升级前用 SQLite backup 保存 `artifacts/stage2-before-v2.sqlite3`。完整文件哈希、迁移前后计数和来源元数据见 `artifacts/stage2-readiness.json`；schema 导出为 `artifacts/manual-confirmation.schema.json`。`PRAGMA integrity_check` 返回 `ok`。人工确认与任务的测试记录只在隔离测试库中。

**图片限制**：D1 的 46 个绘图对象已定位并标记未解析；没有 OCR、图片文字/图纸尺寸核验或 PDF 解析。DOCX 页码没有渲染验证；自动编号无法可靠识别的子节不补造，保留可识别上级章节与原引用章节。JSON 后备不含图片位置，图片数量仅为其元数据声明。

## 阶段 2 修改文件

新增：

- `src/cf_stitch/knowledge/documents.py`、`parameters.py`、`retrieval.py`
- `src/cf_stitch/services/evidence.py`、`src/cf_stitch/knowledge_ui.py`
- `scripts/streamlit_entry.py`
- `tests/test_documents.py`、`test_parameters.py`、`test_evidence_storage.py`、`test_evidence_app.py`

修改：

- `src/cf_stitch/storage/database.py`、`src/cf_stitch/ui.py`、`src/cf_stitch/services/__init__.py`
- `scripts/launch.py`、`tests/streamlit_worker.py`、`tests/test_launch.py`、`tests/test_storage.py`
- `README.md`、`docs/SOURCE_AUDIT.md`、`docs/PROGRESS.md`、`pyproject.toml`、`requirements.txt`、`.env.example`（依赖版本未变）

本地验收产物位于忽略目录 `artifacts/`，包括数据库备份、readiness/schema JSON、JUnit XML、隔离数据库和临时浏览器验收控制脚本/日志；不是实验结果或训练数据。

## 阶段 2 实际测试

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q --basetemp artifacts/pytest-stage2-final --junitxml=artifacts/stage2-tests-final.xml
```

**177 passed in 17.44s，0 failed，0 skipped。**

| 检查组 | 数量 | 核心覆盖 |
|---|---:|---|
| 领域 schema | 35 | 阶段 1 领域分离及真实性约束回归 |
| 种子与原存储 | 26 | 原值保真、只读、任务、版本/数据隔离回归 |
| DOCX / 后备读取 | 20 | 两份真实原件逐块比对、图片标记、行位置、原件优先/损坏/缺失、DTD/实体/UTF-16、ZIP 边界、外部关系和命令不执行 |
| 参数语义 | 37 | 六范围/条件、p/s/机械间距、供纱/布面、5–10 mm 非禁区、七类身份、条件推导、六项待确认、缺出处、独立确认校验 |
| 新证据存储/检索 | 32 | v1→v2保留任务、字面中文检索、精确快照/哈希、缺证据、SQL/文档注入、采集路径变化、只读、事务回滚、确认引用隔离 |
| 启动 | 15 | Windows bat、默认本地参数、占用端口保护、真实 HTTP 200/健康、关闭释放端口、主线程表格依赖初始化及真实中文表格往返 |
| Streamlit AppTest | 12 | 原五页回归及资料五入口、范围分类、全文/空结果、来源/待确认、确认持久化、缺文件保留旧库/空库草案 |

`pip check` 实际返回 `No broken requirements found.`。本阶段不将 AppTest 或 HTTP 健康检查冒充全部浏览器流程。

本轮实测发现并修复：缺来源导致旧版全站停止；历史版本选择误取同哈希的新抽取；Windows 首次表格渲染时 NumPy 原生模块导入与服务线程启动等待。最后一项通过主线程预加载现有 NumPy/Pandas/PyArrow 修补，正常启动器和测试服务共用入口，并补了全新进程测试。原诊断堆栈保存在隔离浏览器日志中。

## 阶段 2 浏览器与剩余范围

在 Codex 内置浏览器及隔离数据库 `artifacts/stage2-browser-check-03/data/`，使用临时本地端口 52896 复测。默认端口仍为 8502；验收时其绑定暂不可用，因此选择空闲端口，没有停止原占用者或更改用户配置。

已实际检查：中文工作台、资料页表格完成加载、D2 六项范围与原表、张力检索区分供纱/细纱/布面、来源哈希差异和图片未解析提示；全文检索“细纱”实际命中 `D2:t002` 与 `D2:t002:r005`，显示完整 SHA-256、章节及原文适用条件；无匹配词后旧命中消失，显示“未找到证据”；六项待确认标题均可见，展开链式/双线自锁后同时显示 `D1:p0147`、`p0166`、`p0167` 原句及后备版本限制，状态仍为 pending。

尚未测试：Chrome/Edge 独立浏览器、完整移动端布局、屏幕阅读器、表格下载/列菜单/全屏、多用户并发；人工确认提交与重开通过 AppTest 验证，尚未在真实浏览器提交。来源版本下拉与手输块 ID 的完整浏览器操作未覆盖，精确快照定位由存储测试、空位置由 AppTest 验证。没有云端 API、LLM、PDF/OCR 或真实设备试验。

收尾：临时浏览器页已关闭，三次本轮验收服务均通过其独立 stdin 停止通道退出，返回码 0 并检查对应端口释放。最后默认 `scripts/launch.py --check` 报告 **8502 被占用**，没有停止占用程序；依赖检查仍通过。可在当前 PowerShell 临时设置 `CF_STITCH_PORT=8503`，该端口启动检查实际通过；应用默认配置仍为 `127.0.0.1:8502`，没有修改系统环境或保持验收服务运行。

启动仍为双击 `start.bat` 或使用 README 中 `scripts/launch.py` 命令。不要绕过新启动入口直接调用 `python -m streamlit`，否则不包含本机表格初始化修补。阶段 3 的计算/规则、阶段 4 的实验记录、阶段 5–7 的训练/优化/编排继续保留未实现状态，等待下一阶段口令。

---

以下为阶段 1 原始交付记录；其中“尚未实现”描述是 2026-09-18 当时状态，当前以阶段 2 记录为准。

## 项目边界与准备核查

- 当前独立项目根目录：`C:\Users\22846\Desktop\碳纤维复合材料缝合技术\CF-Stitch Agent 开发交接包`。
- 项目根目录与上层工作目录均不是 Git 仓库；已运行 `git rev-parse --show-toplevel` 和 `git status --short`，返回 `not a git repository`。不能声明 Git 工作区干净；未初始化 Git、提交或推送。
- 未访问或修改已有铝合金焊接 Agent；依赖和数据库均在本项目内。不修改系统代理、全局 Python 包或全局 Streamlit 设置，不公开部署。
- 开始阶段前重新读取 AGENTS、PROJECT_SPEC、SOURCE_AUDIT、两份 spec YAML、manifest、原进度和阶段 1 口令。
- 两份 DOCX 只读保留。D2 实际 SHA-256 与 manifest 一致；D1 实际 SHA-256 为 `7ad8d0cd20914c20a708544ebbf4e93ad676b8adc843c3fecc5e0fccd61b51a9`，manifest 仍为 `f97ccd33c4afa780aa76ec441c5d8454e6229cc8b6addd4996f1b9812f04853b`。此前 255 个非空正文段落比较一致，不能认定文件身份一致，图片与工程尺寸未核验。本轮再次只读计算哈希，未改原件、manifest、抽取 JSON 或种子值。

## 阶段 1 已实现

1. **独立环境与入口**：Python `.venv`，模块化 `src/cf_stitch`，中文 Streamlit 界面、README、依赖锁定文件、`.env.example`、`.gitignore`、Windows `start.bat` 和启动检查器。
2. **领域对象分离**：材料、构型、缝合机制、路径、运动平台、工艺参数、设备来源声明、人工确认设备配置、目标与测量结果分别定义。机器人不在缝合机制枚举中，人字形只属路径；无底线自锁必须保留原名和 `custom_unconfirmed`。
3. **数据语义**：数值保留单位、原值/原单位、测量阶段、设定/实测性质与来源；缺失值有原因。负数/零针距被拒绝；未缝合 p/s 为空。初始窗口不作为上下限校验依据，不将超出文档窗口直接判为数学非法。
4. **设备与结果分离**：来源声明固定为 `source_only`、`advisory`；人工设备配置必须有审批人、带时区时间及理由。目标不自动写成测量结果，演示来源不能进入真实任务空间。阶段 1 不提供设备审批流程或模型训练入口。
5. **SQLite v1**：事务迁移、版本记录、外键、JSON 校验；33 条参数声明幂等导入并保存原始 YAML、manifest 和包内哈希。来源表禁止更新/删除，种子变化拒绝静默覆盖。草案保存前重新经过 Pydantic 验证，真实与演示分别统计。
6. **五个界面入口**：任务工作台可录入材料/构型、机制、路径、平台、候选参数和目标，并保存/查看草案；资料参数支持只读查看和字段筛选；实验设计仅展示七组原模板；数据管理显示实际计数及 `not_ready`；设置展示本地路径与结构版本。未实现功能没有假成功按钮。
7. **本地启动**：默认 `127.0.0.1:8502`，检查独立解释器与端口；被占用则报告，不终止其他服务。关闭 Streamlit 使用统计，隐藏开发/部署工具栏。阶段 1 不加载 API Key，不创建云端请求。

## 修改与新增文件

修改：

- `README.md`
- `docs/PROGRESS.md`

新增：

- `app.py`
- `pyproject.toml`、`requirements.txt`、`requirements-lock.txt`
- `.env.example`、`.gitignore`、`.streamlit/config.toml`、`start.bat`
- `scripts/launch.py`
- `src/cf_stitch/__init__.py`、`src/cf_stitch/config.py`、`src/cf_stitch/ui.py`
- `src/cf_stitch/domain/__init__.py`、`src/cf_stitch/domain/schemas.py`
- `src/cf_stitch/knowledge/__init__.py`、`src/cf_stitch/knowledge/seeds.py`
- `src/cf_stitch/storage/__init__.py`、`src/cf_stitch/storage/database.py`
- `src/cf_stitch/services/__init__.py`、`src/cf_stitch/models/__init__.py`、`src/cf_stitch/optimization/__init__.py`、`src/cf_stitch/providers/__init__.py`（后续模块位置，无假实现）
- `tests/test_domain.py`、`tests/test_storage.py`、`tests/test_app.py`、`tests/test_launch.py`、`tests/streamlit_worker.py`
- `data/.gitkeep`、`artifacts/.gitkeep`

本地运行产物（不提交）：`.venv/`、`data/cf_stitch.sqlite3`、测试缓存、`artifacts/pytest-*`、`artifacts/browser-check/`、`artifacts/stage1-tests.xml`、`artifacts/stage1-readiness.json`、`artifacts/research-task.schema.json`。

## 实际环境与测试结果

实测 Windows / Python **3.12.14**；`.venv` 的 `include-system-site-packages=false`。

| 依赖 | 实际版本 |
|---|---|
| Streamlit | 1.64.0 |
| Pydantic | 2.13.5 |
| PyYAML | 6.0.3 |
| pytest | 9.1.1 |
| SQLite（Python 内置） | 3.53.1 |

最终完整测试命令（在项目根目录执行）：

```powershell
& '.\.venv\Scripts\python.exe' -X utf8 -m pytest -q --basetemp artifacts/pytest-final-02 --junitxml=artifacts/stage1-tests.xml
```

**结果：80 passed in 10.29s，0 failed，0 skipped。**

| 检查组 | 数量 | 实际覆盖 |
|---|---:|---|
| 领域 schema | 35 | 机制/路径/平台分离、无底线自锁、空值原因、有限数值、负针距/零针距、单位、未缝合组、证据身份、目标/测量分离、人工确认必填项、demo 隔离 |
| SQLite 与来源种子 | 26 | YAML 解析、33 条声明保真、引用块存在、幂等导入、只读触发器、哈希改变拒绝、事务回滚、非法种子拒绝、七组空结果、草案重开读取、命名空间、未知版本保护 |
| 启动与配置 | 14 | 本地地址、数据路径边界、端口检查且原监听仍在、Windows bat 检查、真实 Streamlit 健康接口与首页 HTTP 200、测试服务正常退出并释放端口 |
| Streamlit AppTest | 5 | 无 API 五页渲染、参数筛选、带上下文草案保存/新会话重读、非法输入与未缝合输入保护、初始化失败显示错误并停止 |

其他实际检查：

```powershell
& '.\.venv\Scripts\python.exe' -m pip check
& '.\.venv\Scripts\python.exe' -X utf8 scripts/launch.py --check
```

分别返回 `No broken requirements found.` 与本项目解释器/本地 8502 启动检查通过。

默认数据库首次初始化实际结果：结构版本 **1**、来源 **2**、参数 **33**；真实及演示命名空间的任务、实验、测量、人工确认设备、已训练模型计数均 **0**。测试草案不在默认数据库中。

开发过程中确实修复过的问题：初次环境说明读取未指定 UTF-8，随后启动命令与读取统一显式编码；Windows 启动验收最初只停止虚拟环境父进程，补端口释放断言后暴露子进程生命周期问题，曾出现启动用例失败。现已采用测试专属 stdin 管道通知真实 Streamlit CLI 正常退出，并检查端口关闭；最终 80 项全通过。期间精确清理本轮测试进程，未按名称批量停止其他 Python 或其他项目。

## 真实浏览器检查

使用 Codex 内置浏览器，在 `127.0.0.1:8502` 和隔离数据库 `artifacts/browser-check/data/cf_stitch.sqlite3` 实际检查：

- 中文工作台加载和桌面布局。
- 录入测试材料/构型、选择无底线自锁机制、人字形路径、机器人平台，填写 p/s 并点击保存。
- 成功保存本地测试草案，刷新后仍可从任务列表和详情入口找到同一任务。
- 资料页展示 33 条来源参数、`initial_trial_window`/`source_only`/`advisory` 和 D1 版本差异提示。
- 实验设计页仅展示来源七组方案；数据管理页显示草案 **1**，真实实验/测量/模型/确认设备均 **0**。
- Windows 监听表实际返回 `127.0.0.1:8502`。临时浏览器页和验收服务已关闭；最终启动检查再次确认 8502 可用。

**未测试的浏览器行为**：Chrome/Edge 独立浏览器、手机/窄屏、键盘与屏幕阅读器完整无障碍流程、表格 CSV 下载/全屏/列菜单、多用户并发。默认用户数据库未通过浏览器写入任何测试草案。

AppTest 结果不等同于浏览器端到端覆盖；HTTP 健康检查不等同于所有按钮测试。没有进行真实云 API 或本地 LLM 测试，因为阶段 1 没有这些功能。

## 启动方式

在本目录双击 `start.bat`，再打开 `http://127.0.0.1:8502`。或：

```powershell
Set-Location 'C:\Users\22846\Desktop\碳纤维复合材料缝合技术\CF-Stitch Agent 开发交接包'
& '.\.venv\Scripts\python.exe' -X utf8 scripts/launch.py
```

`.env.example` 仅为配置示例，不自动加载 `.env`；目前仅读取进程环境中的 `CF_STITCH_PORT` 与 `CF_STITCH_DATA_DIR`。无需 API Key。当前没有保持运行的验收服务。

## 已知限制与下一阶段

- 任务目前为新增/查看草案，不支持编辑、删除或方案批准；材料与工艺表单只暴露基础字段，完整字段已在领域对象中定义。
- 数据管理为基础计数，不支持 CSV/Excel 导入、测量记录或数据集管理；七组模板只读，不生成实验，也不代表训练样本。
- 来源加载只检查包内一致性，D1 原件版本差异仍待解决；全文检索、原始 DOCX 解析、图片核验、冲突处置在后续阶段。
- 尚无几何计算、设备可行性规则引擎、模型训练、预测、不确定度、优化或 LLM 工具编排。
- scikit-learn 尚未安装；阶段 5 再实现与测试。没有合格真实数据时，真实模型仍须保持 `not_ready`。
- SQLite v1 目前仅支持首次建库与同版本重开；未来结构/种子更新须添加明确迁移，不能清空用户数据库解决版本冲突。
- 私有原文和抽取 JSON 被 `.gitignore` 排除；若将来仅克隆代码，需另行提供授权的本地来源文件。当前完整本地目录可直接运行。

下一步仅在收到用户阶段 2 口令后，读取对应规范实施证据检索与参数来源管理。
