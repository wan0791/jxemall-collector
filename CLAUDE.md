# 江西电子卖场数据自动抓取

## 项目概述

从江西省政府采购电子卖场 (jxemall.com) 合同公告中手动采集电脑销售数据，覆盖南昌、九江、上饶、鹰潭、景德镇、宜春、萍乡 7 城所有区县。

## 技术栈

- **采集**: Python + requests + 自定义 SSL 适配器（jxemall 需 UnsafeLegacyRenegotiation）
- **存储**: PostgreSQL 16 (Docker 容器)
- **Web**: Flask + psycopg2 + Jinja2 Dashboard
- **导出**: openpyxl → Excel
- **部署**: Docker Compose（postgres + app 双容器）→ 192.168.180.210

## 目录结构

```
├── app.py               # Flask Web Dashboard（启动入口）
├── collector.py          # API 采集 + content HTML 解析
├── db.py                 # PostgreSQL CRUD（psycopg2 + 连接池）
├── config.py             # 配置（区县代码、DB连接、筛选规则）
├── exporter.py           # Excel 导出
├── lock.py               # 任务锁
├── Dockerfile            # 应用镜像
├── docker-compose.yml    # postgres + app 双容器编排
├── deploy.sh             # 部署脚本
├── requirements.txt      # Python 依赖
├── CLAUDE.md             # 本文件
├── .gitignore
├── templates/
│   ├── index.html
│   └── login.html
└── data/                 # 运行时生成（task.lock）
```

## 启动方式

### Docker 部署（推荐）
```bash
cd 江西电子卖场数据自动抓取
docker compose up -d
# 访问 http://<ip>:5050
# 默认账号: admin / admin123
```

### 本地开发
```bash
# 先启动 PostgreSQL
docker run -d --name pg-jxemall -e POSTGRES_DB=jxemall -e POSTGRES_USER=collector -e POSTGRES_PASSWORD=collector123 -p 5432:5432 postgres:16-alpine

pip install -r requirements.txt
python app.py
# 访问 http://127.0.0.1:5050
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DB_HOST | 127.0.0.1 | PostgreSQL 地址 |
| DB_NAME | jxemall | 数据库名 |
| DB_USER | collector | 用户名 |
| DB_PASSWORD | collector123 | 密码 |
| DB_PORT | 5432 | 端口 |
| SECRET_KEY | (内置默认) | Flask session 密钥 |

## 数据采集

仅支持手动采集，通过 Web Dashboard 操作：
1. 登录 Dashboard
2. 点击「手动采集」
3. 选择日期范围和目标城市
4. 点击「开始采集」

## API 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| / | GET | 主页 Dashboard |
| /api/contracts | GET | 查询记录 |
| /api/collect | POST | 手动触发采集 |
| /api/progress | GET | 采集进度轮询 |
| /api/export | GET | 导出 Excel |
| /api/stats | GET | 统计信息 |

## 目标 API

```
POST https://www.jxemall.com/announcement/lobby/queryPage
```

合同公告类型: [9005, 8001, 9001, 3010]

## 注意事项

1. API 仅支持区县级 district code，城市级返回 null
2. 每条合同可能包含多行商品，展开存储
3. 电脑筛选为关键词匹配，Dashboard 中可切换查看全部数据
4. 串行号 (serialNum) 为唯一去重键
