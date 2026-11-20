<div align="center">

# NEWLAND APS
### 新大地 · 生产计划与执行工作台

从订单与库存出发，让排程、审批、派工和报工进入同一条业务流程。

**React 19 · TypeScript · FastAPI · SQLAlchemy · PostgreSQL / SQLite**

[快速启动](#快速启动) · [核心能力](#核心能力) · [系统架构](#系统架构) · [操作指南](docs/企业工作台.md) · [开发协作](CONTRIBUTING.md)

</div>

---

> **当前阶段：可运行的企业试点版本。** 已实现前后端、排程内核和车间执行闭环。生产环境接入、企业身份认证和 AI 助手仍在后续计划内。公开仓库仅提供源码、文档及合成演示数据生成器。

## 项目解决什么问题

制造企业的生产计划需要同时处理交期、库存、线体适配和换型；计划发布后，还要接收现场的真实完成量，才能生成可信的下一版计划。

Newland APS 把这一过程组织为可追溯的工作流：计划员生成和调整草案，主管审批发布，车间签收与报工，系统根据实际产量安排剩余任务。每次修改形成新的计划版本，便于比较、核对和导出。

## 核心能力

| 工作环节 | 已实现能力 |
|---|---|
| 数据接入 | Excel 导入、表头及数据校验、库存与安全库存对账、异常清单 |
| 自动排程 | 确定性规则基准、多目标候选方案、产线适配、日历与换型约束 |
| 计划调整 | 甘特图拖动、指定产线与时间、任务锁定、约束校验、新草案版本 |
| 方案比较 | 准交率、加权延期、换型时间、负载和未排任务对比 |
| 审批派工 | 提交审批、批准、发布、车间签收、操作日志与版本检查 |
| 现场执行 | 实际开工、结束、合格完成量登记及更正 |
| 动态重排 | 紧急插单、设备停机、物料延期、按真实报工安排剩余量 |
| 数据导出 | 需求对账、作业计划、订单交付、换型与异常 Excel |
| 账户与隔离 | 计划员、审批主管、车间角色；工厂范围的访问检查 |
| 后台任务 | 独立 Worker、幂等请求、任务认领超时与过期结果保护 |

### 一条完整的业务流程

~~~mermaid
flowchart LR
    A["导入订单与库存"] --> B["对账与异常检查"]
    B --> C["生成与调整草案"]
    C --> D["审批与发布"]
    D --> E["车间签收"]
    E --> F["实际报工"]
    F --> G["剩余量重排"]
    G --> C
~~~

企业工作台会检查计划使用的报工依据是否仍然有效；报工更新后，旧依据生成的重排版本需要重新核对，避免用过期产量发布计划。

## 系统架构

~~~mermaid
flowchart TB
    U["浏览器 · React / TypeScript / Ant Design"] --> API["FastAPI · 登录、角色与工厂范围"]
    API --> DB[("SQLite / PostgreSQL")]
    API --> JOB["排程任务队列"]
    JOB --> WORKER["独立 Worker"]
    WORKER --> CORE["排程内核与约束校验器"]
    WORKER --> DB
    XL["Excel · 订单 / 库存 / 产线 / 日历"] --> API
    API --> OUT["版本、操作记录与 Excel 导出"]
~~~

| 部分 | 技术与职责 |
|---|---|
| 前端 | React 19、TypeScript、Vite 7、Ant Design 6；计划、审批与车间工作界面 |
| 服务端 | Python、FastAPI；账户会话、业务流程、导入导出与任务 API |
| 数据层 | SQLAlchemy 2、Alembic；本地 SQLite，容器部署 PostgreSQL |
| 排程内核 | Python 规则基准及局部搜索；独立约束校验 |
| 部署 | Docker 多阶段构建；API、Worker、迁移和数据库分离 |

## 快速启动

建议使用 **Python 3.12**、**Node.js 24** 和 Git。以下命令在项目根目录的 PowerShell 中执行。

~~~powershell
git clone https://github.com/syh244810321-cell/newland-aps.git
cd newland-aps

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-product.txt
npm --prefix frontend ci
npm --prefix frontend run build

.\.venv\Scripts\python.exe scripts/create_demo_workbook.py
.\.venv\Scripts\python.exe -m aps.product serve --demo --source .var/demo.xlsx
~~~

打开 **http://127.0.0.1:8010/**，选择计划员账户。导入生成的演示工作簿后，可按下方流程体验系统。

1. 检查数据接入页的异常与需求对账。
2. 新建排程，比较候选方案，在甘特图中调整或锁定作业。
3. 提交审批，切换为审批主管，批准并发布。
4. 切换为车间角色，签收计划并登记实际完成量。
5. 按报工生成剩余量重排，核对新旧版本并导出。

演示生成器使用固定的虚构客户、订单、库存和产线；所有演示日期仅用于回放。演示登录用于本地体验，正式使用需创建独立账户。

### 开发和验证

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_public_source.py
npm --prefix frontend run build
~~~

前端热更新使用 `npm --prefix frontend run dev`；后端另行启动在 8010 端口，Vite 已配置 API 代理。

### 正式账户与容器部署

~~~powershell
.\.venv\Scripts\python.exe -m aps.product create-user planner 计划员 planner
~~~

命令会交互式读取密码。Docker Compose 的数据库变量、HTTPS Cookie、迁移和 Worker 配置见 [企业工作台说明](docs/企业工作台.md)。不要在公网启用演示登录。

## 数据与隐私

- 原始企业 Excel、真实订单、客户明细、数据库、运行导出和环境密钥不随源码分发。
- `.gitignore` 排除运行目录、电子表格、数据库、密钥文件、日志及压缩包。
- 发布前运行 `scripts/check_public_source.py`，检查 Git 跟踪文件中的常见密钥、个人邮箱和敏感文件类型。它是基础检查，不能替代人工审阅。
- 提交身份使用 GitHub 的 `users.noreply.github.com` 邮箱。公开仓库中的源码、文档和提交记录可被其他人下载和保存。
- 企业真实数据在本地或私有部署环境导入。代码仓库公开不会自动上传本机运行数据。

## 当前边界与后续工作

| 方向 | 当前状态 |
|---|---|
| 排程与执行闭环 | 已实现，需结合企业规则与验收数据持续验证 |
| PostgreSQL | 已提供部署配置与迁移；仍需目标环境的部署和恢复演练 |
| ERP / MES | 目前通过 Excel 和手工报工接入，企业连接器待对接 |
| 企业身份认证 | 当前为独立账户，SSO 与企业目录待接入 |
| AI 助手 | 尚未接入 LLM；后续用于解释排程证据、问数和试算建议 |
| 商业化 | 试点、性能压测、监控告警、运维与交付规范仍需完善 |

排程结果由明确的业务规则与约束校验支撑；未来 AI 建议仍需经过相同的权限和约束检查。项目尚未提供多租户计费或商业 SLA。

## 目录导航

~~~text
aps/                    排程内核、早期演示与企业后端
  product/              企业 API、业务服务与数据表
frontend/               企业工作台前端
migrations/             数据库迁移
scripts/                合成演示数据与公开源码检查
tests/                  排程与业务流程回归
docs/                   操作、演示和约束说明
compose.yaml            PostgreSQL / API / Worker 部署
~~~

## 文档与协作

- [企业工作台：启动、角色、审批与报工](docs/企业工作台.md)
- [现场演示流程](docs/现场演示.md)
- [排程约束及指标口径](docs/约束及指标.md)
- [开发协作与提交规范](CONTRIBUTING.md)
- [公开发布与隐私处理说明](docs/公开发布.md)

队友通过分支和 Pull Request 提交修改，由项目维护者审核合并。功能问题可以提交 Issue，安全问题请先通过 GitHub 的私密安全报告入口联系维护者（仓库启用该功能后可用），不要在公开 Issue 中粘贴真实数据或密钥。
