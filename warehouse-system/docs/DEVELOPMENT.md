# 开发说明

## 环境与目录

应用根目录是 `warehouse-system/`，前端在 `frontend/`，后端在 `backend/`。

需要 Python 3.12、Node.js 22.12 或更高版本、npm、Git，以及支持 CHECK 约束的 MySQL 8.0.16 或更高版本。隔离测试脚本使用 MySQL 8.4。

后端依赖锁定在 `backend/requirements-lock.txt`，前端依赖锁定在 `frontend/package-lock.json`。

## 安装依赖

从应用根目录打开 PowerShell：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
cd ..\frontend
npm.cmd ci
Copy-Item .env.example .env
```

示例配置不包含真实密码。在本地配置专用开发数据库、运行账号、迁移账号和随机认证密钥；详细配置项见各 `.env.example`。真实配置、凭据与业务数据不提交到仓库。

## 数据库与接口

数据库结构由 `backend/alembic/versions/` 中的迁移管理。库存变更通过业务接口记账，同时维护单据和流水；新增结构使用新的迁移文件。

`backend/scripts/bootstrap_mysql.py` 用于准备新的开发数据库。执行初始化前核对数据库名和目标实例，已有业务数据的环境按对应升级流程处理。

## 隔离测试

在应用根目录运行下面的命令，创建专用的 MySQL 测试实例和应用副本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare-isolated-test.ps1 -RunName e2e-local -Port 33312
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-isolated-tests.ps1 -IsolatedBackend .\.runtime\e2e-local\source\backend
```

需要浏览器验证时启动隔离应用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-isolated-app.ps1 -RunName e2e-local -ApiPort 18080
```

测试和迁移检查使用独立实例与合成数据。结束后按脚本参数停止并清理本次测试实例。

前端测试在 `frontend/` 中运行：

```powershell
npm.cmd test
npm.cmd run build -- --outDir ../.runtime/frontend-preview
```

预览构建输出到本地 `.runtime/`，与已经运行的前端资源分开保存。

## 源码部署

后端可以同时提供前端构建资源和 `/api` 接口。配置项见 `backend/.env.production.example` 与 `frontend/.env.production.example`；服务启动、停止和数据库备份工具位于应用根目录。

服务端按账号角色检查权限，浏览器页面通过同一服务入口访问。手机页面使用 `/m`，相机功能需要 HTTPS。

## Windows 安装工具

`packaging/windows/` 包含安装器构建脚本、运行控制程序和版本兼容清单。构建需要 Inno Setup、微软运行库及相应运行组件；具体参数以脚本为准。

版本清单和文件校验值用于限定安装内容、核对兼容性。修改安装版本时一并核对源码组合、校验清单与已有数据的升级路径。
