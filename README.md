# 平台系统目录说明

本目录（`C:\ZSK\XiTong`）用于存放知识库平台的运行代码、虚拟环境、数据库与运行时文件，与知识数据目录 `C:\ZSK\ZhiShi` 分离。

## 目录结构

```
C:\ZSK\XiTong
├── venv/                   # Python 虚拟环境
├── kb/                     # Django 项目配置包
├── core/                   # 核心应用（模型/配置/上下文）
├── dashboard/              # 总览模块
├── starmap/                # 知识星图模块
├── wiki/                   # WiKI 层模块
├── bookshelf/              # 书架模块
├── knowledgebase/          # 知识库模块
├── inspiration/            # 灵感库模块
├── selfmedia/              # 自媒体模块（占位）
├── office/                 # 办公平台模块
├── collection/             # 知识收集模块
├── runarchive/             # 运行档案模块
├── systemsettings/         # 系统设置模块
├── templates/              # HTML 模板
├── static/                 # 静态文件（CSS/JS）
├── static_collected/       # collectstatic 输出
├── media/                  # 用户上传文件中转
├── logs/                   # 平台运行日志
├── temp/                   # 临时文件
├── db.sqlite3              # SQLite 数据库
├── requirements.txt        # Python 依赖清单
└── manage.py               # Django 管理脚本
```

## 启动命令

```powershell
cd C:\ZSK\XiTong
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

## 目录约定

- `C:\ZSK\XiTong`：只存平台本身，不存知识内容。
- `C:\ZSK\ZhiShi`：存全部知识资产（原始资料、生成内容、运行档案；运行档案已整合原平台搭建文档）。

---

# 智识库平台（源码仓库）

个人知识管理平台 —— Django 5.2 单体应用，前后端同构渲染，本地单机运行，数据完全自持。

## 功能模块

| 模块 | 应用 | 说明 |
|---|---|---|
| 总览 | `dashboard` | 平台数据概览首页 |
| 知识库 | `knowledgebase` | 知识条目浏览、检索、双链关系 |
| 知识收集 | `collection` | 文本 / 本地文件 / 网页 / 图片 / 视频导入入库 |
| WiKI 层 | `wiki` | 对知识库与办公产物做二次加工，产出 Wiki 层知识 |
| 知识星图 | `starmap` | 节点-边关系可视化 |
| 书架 | `bookshelf` | 书籍登记、阅读笔记、金句摘录 |
| 灵感库 | `inspiration` | 灵感速记与加工日志 |
| 办公平台 | `office` | 工作记录 → AI 献策 → 生成报告 → 分类标签 → 文件目录 |
| 运行档案 | `runarchive` | 平台运行档案归档 |
| 系统设置 | `systemsettings` | 平台配置与**备份/迁移** |

## 环境要求

- Windows 10/11（其他平台未验证）
- Python **3.10+**（实测 3.13 / 3.14 可用；Django 5.2 官方支持范围为 3.10–3.13）
- 无需 Node.js、无需外部数据库（内置 SQLite）

## 快速开始

### 方式一：本机已有环境

```powershell
cd C:\ZSK\XiTong
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

或直接双击 `启动智识库.bat`（停止用 `停止智识库.bat`）。

### 方式二：全新机器（从零搭建）

```powershell
cd <仓库目录>
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe init_config.py
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

安装脚本会自动创建 `logs/`、`media/` 等运行时目录，无需手工准备。

## 依赖说明

依赖分两层，按需安装：

- **`requirements.txt`** —— 核心依赖，平台所有功能正常运行所必需（Django、Pillow、PyPDF2、python-docx、openpyxl、lxml、dashscope 等），体积小、纯 wheel。
- **`requirements-optional.txt`** —— 可选重型依赖（`yt-dlp`、`moviepy`、`openai-whisper`、`pytesseract` 等），仅在需要视频下载、转码、语音转写、OCR 时安装。

## 数据目录

平台代码与知识数据**物理分离**，便于单独备份与迁移：

- 代码：`<root>\XiTong`
- 数据：`<root>\ZhiShi`（可用环境变量 `ZHISHIKU_DATA_ROOT` 覆盖）

## 备份与迁移

「系统设置 → 备份与迁移」提供三类独立备份：

| 类型 | 内容 | 用途 |
|---|---|---|
| 知识备份 | `ZhiShi` + `media` + 16 张知识表导出 | 只保知识资料 |
| 系统备份 | 全部程序代码 + 空数据骨架 + 一键安装器 | 生成不含知识数据的空平台，可发给他人搭建 |
| 迁移备份 | 代码 + 全部知识资料 + `db.sqlite3` | 换机整包搬迁，解压后一键恢复 |

备份输出目录默认为 `<root>\备份输出`，可用环境变量 `ZHISHIKU_BACKUP_ROOT` 覆盖。

## 未纳入版本控制的内容

以下由 `.gitignore` 排除，克隆后需自行重建或另行获取：

| 排除项 | 原因 / 重建方式 |
|---|---|
| `venv/` | 含本机绝对路径，换机无效；用 `python -m venv venv` 重建 |
| `bin/` | ffmpeg 等外部二进制（约 826 MB），按需自行放置 |
| `db.sqlite3` | 数据库文件，含个人数据；用 `manage.py migrate` 重建 |
| `media/`、`logs/` | 运行时数据与日志，程序启动时自动创建 |
| `static_collected/` | 由 `manage.py collectstatic` 生成 |
