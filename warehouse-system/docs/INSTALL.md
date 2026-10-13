# Windows 本地部署教程

本仓库仅提供源码和教程，不提供安装包。下面从源码部署一套新的本地系统，默认地址为 `http://127.0.0.1:8080`。

适用于 Windows 10/11 64 位电脑的首次部署。请使用新的源码目录、数据库和数据库账号；已有业务库的更新需要先备份并制定升级步骤，不要直接套用首次初始化教程。

## 1. 准备运行环境

| 软件 | 版本与安装说明 |
| --- | --- |
| [Git](https://git-scm.com/install/windows) | 安装后可以在 PowerShell 中使用 `git` |
| [Python](https://www.python.org/downloads/windows/) | 使用 3.12 系列，安装时启用 Python Launcher；需带 pip 和 venv |
| [Node.js](https://nodejs.org/en/download) | 使用 22.12 或更高版本，自带 npm |
| [MySQL Community Server](https://dev.mysql.com/doc/refman/8.4/en/windows-installation.html) | 建议 8.4，安装为 Windows 服务，记住自己设置的 root 密码；下面使用端口 3306 |

安装完成后重新打开 PowerShell，检查：

```powershell
git --version
py -3.12 --version
node --version
npm.cmd --version
```

如果命令不存在，检查安装选项和 PATH；确认 Windows“服务”中的 MySQL 服务已启动。Python 3.12 后续版本的发行方式以官网为准，请选择可用的 Windows 运行环境。

## 2. 下载源码

下面用 `D:\WMS-local` 作为示例。没有 D 盘时，可以换成 `C:\WMS-local`，后续路径也要一起调整。选择一个尚不存在的目录。

```powershell
git clone https://github.com/Rhwjtw/light-warehouse-wms.git D:\WMS-local
cd D:\WMS-local\warehouse-system
```

后续服务脚本都在 `warehouse-system` 目录中，不在仓库顶层。

## 3. 安装后端依赖并填写配置

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.production.example .env
notepad .env
```

在记事本中修改下面这些配置项，其他项先保留示例值。下列数据库名和账号名应在你的 MySQL 中尚未使用；有同名对象时请换一组新名称。

```dotenv
APP_ENV=production
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=wms_local
MYSQL_USER=wms_local_app
MYSQL_MIGRATION_USER=wms_local_migrator
MYSQL_ADMIN_USER=root
SERVE_FRONTEND=true
FRONTEND_DIST=../frontend/dist
CORS_ORIGINS=[]
API_DOCS_ENABLED=false
WEB_HOST=127.0.0.1
WEB_PORT=8080
WEB_WORKERS=1
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_DISPLAY_NAME=系统管理员
```

还需要在本机 `.env` 中填写：

- `MYSQL_ADMIN_PASSWORD`：安装 MySQL 时设置的 root 密码。
- `INITIAL_ADMIN_PASSWORD`：自己设置的系统登录密码，14～128 个字符，不得包含用户名，也不要使用简单重复密码。妥善保存，后面用于登录。

含特殊字符的密码可用单引号包围；不要在值后面追加行内注释。`MYSQL_PASSWORD`、`MYSQL_MIGRATION_PASSWORD` 和 `AUTH_SECRET` 保持空值，初始化脚本会生成并保存随机值。

数据库密码与系统登录密码用途不同。真实 `.env` 文件只保存在本机，仓库已设置忽略规则。保存并关闭记事本后继续。

## 4. 初始化数据库和管理员

仍在 `backend` 目录，依次运行；每条命令成功后再执行下一条：

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap_mysql.py
.\.venv\Scripts\python.exe scripts\init_admin.py
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic check
```

第一条创建指定数据库、两个专用数据库账号并运行结构迁移，成功后清空 `.env` 中的 `MYSQL_ADMIN_PASSWORD`。第二条创建管理员，成功后清空 `INITIAL_ADMIN_PASSWORD`，不会更改已有管理员密码。不要删除自动生成的应用密码、迁移密码和认证密钥。

`current` 与 `heads` 应显示相同迁移版本，`check` 应无待生成的结构变更。这些命令需要数据库能够正常连接。

初始化得到空业务库及基础权限、配置。日常使用无需运行演示数据脚本 `seed_phase2.py`。

## 5. 构建前端

从 `backend` 目录进入前端：

```powershell
cd ..\frontend
npm.cmd ci
Copy-Item .env.production.example .env
npm.cmd run build
cd ..
```

前端示例配置为 `VITE_API_BASE_URL=/api`，让页面和接口使用同一地址。构建完成后会产生 `frontend/dist/index.html`。前端环境变量会进入浏览器资源，不要在其中填写密码或密钥。

## 6. 检查并启动

现在应位于 `D:\WMS-local\warehouse-system`：

```powershell
.\backend\.venv\Scripts\python.exe .\backend\scripts\check_production.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-prod.ps1
```

检查通过后，启动脚本在后台运行服务并验证页面和数据库健康状态。浏览器打开：

- 电脑版：<http://127.0.0.1:8080>
- 手机版页面：<http://127.0.0.1:8080/m>
- 数据库健康检查：<http://127.0.0.1:8080/api/health/db>

使用第 3 步设置的管理员用户名和登录密码。然后按业务需要创建用户，并维护单位、供应商、库位、原材料、成品与 BOM。

## 7. 日常启动、停止与备份

以后重新打开 PowerShell，先进入应用目录，再按需要执行。电脑重启后需重新启动系统；此教程没有设置开机自启。

启动：

```powershell
cd D:\WMS-local\warehouse-system
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-prod.ps1
```

停止：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-prod.ps1
```

数据库备份：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\backup-db.ps1
```

默认保存到应用目录的 `backups/`，包含业务数据。找不到 MySQL 客户端时，可加参数 `-MySqlBin "C:\Program Files\MySQL\MySQL Server 8.4\bin"`，换成实际安装路径。

备份还应单独保管后端 `.env`，并把资料保存到另一块磁盘。恢复数据库前必须核对目标和现有数据，避免覆盖正在使用的库。

## 8. 手机或其他电脑访问

默认 `WEB_HOST=127.0.0.1` 仅允许本机访问；另一台电脑或手机中的 `127.0.0.1` 指向那台设备自身。

需要局域网共享时，可以停止系统，将后端 `.env` 的 `WEB_HOST` 改为 `0.0.0.0`，重新启动，并在 Windows 防火墙中仅对可信私有网络放行所选网页端口。其他设备访问部署电脑的局域网地址，例如 `http://192.168.1.100:8080/m`；示例 IP 要换成实际地址，MySQL 仍保持本机访问。

手机相机扫码需要 HTTPS、相机权限及浏览器支持。普通局域网 HTTP 地址不能作为手机扫码部署方案；需要扫码时应另行配置可信的 HTTPS 入口。电脑版和手机版使用同一数据库与账号权限。

## 常见问题

| 情况 | 处理方式 |
| --- | --- |
| `py -3.12` 找不到 Python | 安装 3.12 和 Launcher，重开 PowerShell；也可使用已安装的 3.12 可执行文件完整路径 |
| MySQL 连接失败 | 核对服务、主机、端口与 root 密码，确认 `.env` 已保存 |
| 初始化时数据库账号认证失败 | 核对是否误用了已有账号；脚本不会重置已存在账号的密码，应人工确认配置 |
| 管理员初始化提示密码不合规 | 设置 14～128 个字符、无用户名且不易猜测的密码，再运行管理员初始化脚本 |
| 启动提示管理员密码未清空 | 先确认两个初始化步骤均成功，再检查 `.env` 的管理员临时密码项为空 |
| 启动提示找不到前端资源 | 确认前端构建成功、`FRONTEND_DIST=../frontend/dist` 且从应用目录启动 |
| 8080 端口被占用 | 改用一个空闲的 `WEB_PORT`，启动后使用新端口访问；不要直接结束不明进程 |
| 页面或接口启动失败 | 查看 `.runtime/production/logs/` 下的本机日志；分享前去除敏感内容 |

开发与隔离测试见[开发说明](DEVELOPMENT.md)。
