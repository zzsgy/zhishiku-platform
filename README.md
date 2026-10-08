# 智识库平台

个人知识管理与办公平台，使用 Django 5.2、SQLite、本机文件存储、服务器模板与 Markdown 编辑器。包含收集、知识库、WiKI、书架、办公、运行档案与知识星图。

本次基于 main 的 `11b1d4678001156d4afca6a6dca13262b587775a`，按《智识库平台_架构深度评估与优化清单》66 项建议实施。逐项状态与限制见 [优化验收对照表](docs/AUDIT-20261008.md)，升级与恢复见 [运行说明](docs/OPERATIONS.md)。这是保留现有 Django 单体的优化版本，不是重建系统或全库迁移。

## Windows 首次运行

已验证 Python 3.11、3.12。安装完整 Python 后，在源码目录依次执行：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -X utf8 -m pip install --require-hashes -r requirements-bootstrap.txt
.\venv\Scripts\python.exe -X utf8 -m pip install --require-hashes -r requirements.lock
.\venv\Scripts\python.exe -X utf8 manage.py migrate --noinput
.\venv\Scripts\python.exe -X utf8 init_config.py
.\venv\Scripts\python.exe -X utf8 manage.py bootstrap_account
.\launch-platform.ps1
```

没有默认密码。首次创建管理员后，访问 `http://127.0.0.1:8000/` 登录。启动器启动 Waitress 和独立任务 worker，并核对健康接口及监听进程。停止：

```powershell
.\launch-platform.ps1 -Action Stop
```

端口冲突不会结束其它进程，可用 `-Port 8010`。若 PowerShell 执行策略不允许运行脚本，可由项目的 `start-zhishiku.cmd` 启动。

## 数据边界

| 位置 | 作用 |
|---|---|
| 源码目录/db.sqlite3 | 账号、知识节点、版本、关系、任务和配置 |
| 源码目录/media | 上传原件；新原件使用内容哈希，保留原展示名 |
| 源码同级/ZhiShi | 正文导出、办公产物等；数据库是知识正文权威 |
| 源码同级/备份输出 | 含清单与逐文件哈希的备份包 |
| 源码目录/.instance-secret | 独立实例密钥；禁止提交或分发 |
| 系统凭据存储/环境变量 | AI 与 Gitee 的秘密，备份不携带 |

`.env.example` 是环境变量说明，不会被自动加载。完整路径、升级与配置说明见 [运行说明](docs/OPERATIONS.md)。现有用户数据应先备份，在复制的实例或隔离恢复目录升级；不要将源码包覆盖解压到正在使用的实例。

## 本次关键行为

- 业务页面要求登录，设置与备份要求管理员，写请求校验 CSRF；原件展示受保护。
- 原件不可变保存；知识导出使用节点 ID，同名与改名不再共用正文文件。
- 编辑提供版本冲突检测、修订历史和节点回收站；AI 修改现有正文进入审核。
- 视频与备份进入持久化任务，由独立 worker 执行；中断后明确失败而非永久显示运行中。
- 云端 AI 资料外发与自动跨服务降级默认关闭。本机兼容模型可明确设置为无密钥。
- PDF 保留原件，提取逐页文字与页码。复杂多栏、图表、公式及扫描 OCR 尚未完成完整排版保真；页面会提示核对原件。

## 备份与恢复

在设置页创建知识/系统/迁移备份，确保 worker 正在运行。命令行验证与隔离恢复：

```powershell
.\venv\Scripts\python.exe -X utf8 manage.py verify_backup "D:\备份\BACKUP.zip"
.\venv\Scripts\python.exe -X utf8 manage.py restore_backup "D:\备份\BACKUP.zip" --destination "D:\全新恢复目录"
```

目标目录必须不存在。恢复检查通过后再发布到目标目录。凭据需重配，外部工作目录需核对；这是新目录恢复，不提供覆盖旧库或合并导入。仅恢复可信来源；哈希不提供签名或加密。

## 开发验证

```powershell
.\venv\Scripts\python.exe -X utf8 -m pip install -r requirements-test.txt
.\venv\Scripts\python.exe -m playwright install chromium
.\venv\Scripts\python.exe -X utf8 manage.py check
.\venv\Scripts\python.exe -X utf8 manage.py makemigrations --check --dry-run
.\venv\Scripts\python.exe -X utf8 manage.py test --noinput
.\venv\Scripts\python.exe -X utf8 -m pip_audit -r requirements.lock --no-deps --disable-pip
```

CI 配置在 `.github/workflows/checks.yml`，包括 Windows/Ubuntu 与 Python 3.11/3.12；本次仅实际执行了 Windows 环境，不能把配置矩阵写成全平台已验收。

## 可选能力与发行边界

视频下载、FFmpeg、OCR 与 Whisper 需要额外工具/模型，见 `requirements-optional.txt`。本次未安装整套可选工具、未完成其漏洞审计，不把模拟测试当成真实视频/OCR 验收。

当前按个人本机应用设计；账号登录不等于多人数据隔离。网络部署、跨机同步、文档级隐私策略、精确 Token/成本统计、复杂 PDF 保真与成熟度状态机仍需后续实施。依赖清单见 [DEPENDENCIES.json](docs/DEPENDENCIES.json)；原仓库未发现项目级 LICENSE，此次未擅自设置项目许可证，分发前应核对原作者及第三方许可。
