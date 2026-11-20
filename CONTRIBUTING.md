# 开发协作

## 本地准备

按 README 安装后端依赖并构建前端。使用 scripts/create_demo_workbook.py 生成合成数据；不需要企业原始文件即可体验工作台。

## 分支与提交

1. 从 main 拉取最新代码。
2. 使用 codex/功能名 或 feature/功能名 分支开发。
3. 提交前检查 git diff 与 git status，确认没有真实业务数据和密钥。
4. 提交 Pull Request，写清问题、行为变化和验证结果。
5. 维护者审核后合并。不要直接覆盖他人的提交。

请在本仓库配置自己的 GitHub 隐私邮箱：

~~~powershell
git config --local user.name "你的 GitHub 用户名"
git config --local user.email "你的 GitHub noreply 邮箱"
~~~

noreply 邮箱从 GitHub 的 Settings → Emails 获取。不要复制其他队友的提交身份。

## 必要验证

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_public_source.py
npm --prefix frontend run build
~~~

涉及真实排程规则的变更，应说明受影响的约束、单位和指标口径。涉及权限的变更，应检查跨工厂访问以及计划员、主管、车间三个角色的边界。

## 数据边界

- 测试数据必须是合成数据。
- 企业原始表格、数据库、截图中的客户信息和真实生产统计不要提交到仓库或 Issue。
- 不要在代码中填写服务密钥、个人访问令牌或真实账户密码。
- 发现已经提交的密钥时，先撤销或轮换，再处理 Git 历史和缓存。
