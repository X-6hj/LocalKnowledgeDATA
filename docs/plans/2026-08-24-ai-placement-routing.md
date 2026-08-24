# AI 低 Token 资料选址实施计划

> **For Hermes:** 按垂直 TDD 切片逐项实现；每个生产行为必须先有真实失败测试。

**目标：** 让 AI 在添加资料前只获得最多 3 个本地确定性候选目录与重复提示，不再读取完整 catalog 或扫描结果，同时保留可审计的完整结构快照与主题化学习页质量门槛。

**架构：** `library/` 与 `kb_scanner` catalog 继续作为唯一事实来源。新增 `placement_router` 只消费 catalog：一条路径生成精简路由快照，另一条路径按标题/关键词返回小型候选结果；服务、CLI 和 Markdown 输出复用同一纯函数，禁止形成第二套目录模型。根目录 `AGENTS.md` 只注入短流程，不嵌入任何随资料增长的目录树。

**技术栈：** Python 3.14 标准库、`unittest`、现有本地 HTTP 服务、Markdown 项目上下文。

---

## 任务 1：精简 AI 路由快照

**文件：**
- 测试：`tests/test_core.py`
- 新建：`src/placement_router.py`
- 修改：`src/knowledge_structure.py`

1. 先增加失败测试：路由快照只列可复用目录，不展开叶子附件；内容确定、包含 revision 与摘要。
2. 运行目标测试，确认因接口不存在而失败。
3. 实现纯函数 `render_routing_snapshot(catalog)`。
4. 增加与完整快照一致的原子、内容相同不改写能力，并复用通用写入器。
5. 运行目标测试和完整测试。

## 任务 2：候选目录查询与重复提示

**文件：**
- 测试：`tests/test_core.py`
- 修改：`src/placement_router.py`
- 新建：`query_placement.py`

1. 先增加失败测试：中英文关键词能命中最具体的可复用父目录；具体题目叶子不作为父目录候选；同名/近似标题进入重复提示；空查询拒绝；结果限制为 1～5。
2. 运行目标测试并确认预期失败。
3. 实现标准化、词项匹配、可解释评分、稳定排序和精简 JSON 模型。
4. 实现 CLI：`--title`、`--keywords`、`--limit`、`--json`、`--base-dir`；默认输出面向 AI 的短文本。
5. 运行 CLI 单元测试与真实 catalog 查询。

## 任务 3：服务集成与项目自动规则

**文件：**
- 测试：`tests/test_core.py`
- 修改：`src/kb_server.py`、`generate_structure.py`、`run.py`、`.gitignore`
- 新建：`AGENTS.md`

1. 先增加失败测试：服务启动和 revision 变化同时刷新两个固定快照；任一辅助快照失败不阻断 catalog；`GET /api/placement` 复用缓存并校验参数。
2. 实现统一辅助产物刷新；完整快照与路由快照分别降级记录 warning。
3. 增加只读 `GET /api/placement`，限制 query 长度和 limit，不接受文件路径。
4. `generate_structure.py` 一次生成两个固定文件；更新启动提示和忽略规则。
5. 新增短 `AGENTS.md`：先运行候选查询，再局部检查；只有低置信度才读路由快照，极少数情况才读完整快照；严禁直接套用学习页模板。

## 任务 4：学习页差异化硬约束

**文件：**
- 修改：`AI资料整理与放置指南.md`、`docs/HTML学习笔记设计规范.md`、`docs/维护与扩展.md`、`docs/使用手册.md`
- 测试：`tests/test_core.py`

1. 先增加文档契约测试，要求出现“模板仅作安全语义参考、不得复制 DOM/章节顺序、必须先建立题目教学模型和独立视觉方案”等明确约束。
2. 更新规则：模板默认不复制；只提取 CSP、资源限制、可访问性等不随题目变化的底线；页面结构由题目认知负荷、推导关系和复习路径决定。
3. 明确对照近期页面是为了避免重复，不是挑一页继续套版。
4. 运行契约测试。

## 任务 5：文档、版本与验收

**文件：**
- 修改：`README.md`、`docs/架构设计.md`、`docs/维护与扩展.md`、`docs/使用手册.md`、`docs/测试报告.md`、`CHANGELOG.md`

1. 记录 AI 查询优先级、CLI/API、两个快照职责、故障降级、维护约束和扩展边界。
2. 版本更新到 `1.6.0`。
3. 运行 Python 编译、完整单元测试、JavaScript 语法检查。
4. 使用 WSL 与 Windows 便携 Python 运行测试。
5. 在临时知识库验证两次生成：第二次必须“无需改写”；真实服务验证 health、catalog、placement 与两个快照 revision 一致。
6. 检查 Git diff、隐私边界与工作树状态，提交并推送功能分支；读取远端分支确认提交。
