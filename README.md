# 江西电子卖场数据采集系统

从[江西省政府采购电子卖场](https://www.jxemall.com/luban/gongshi)合同公告中采集电脑销售数据，通过 **DeepSeek V4 Flash** 大模型智能识别台式机、笔记本、一体机等电脑硬件采购合同。

## 功能概览

| 功能 | 说明 |
|------|------|
| 手动采集 | 按日期范围和城市从 jxemall API 抓取合同公告（不设定时任务） |
| AI 分类 | DeepSeek V4 Flash 批量判断，30 条/批，10 并发，准确率 >95% |
| Web Dashboard | 数据浏览、筛选、行内编辑、标记关注、批量操作 |
| 用户管理 | admin 可添加/删除/重置密码，多用户协作 |
| Excel 导出 | 按模板格式导出，含公告原文超链接 |
| Token 统计 | 累计展示 AI 分类的 token 消耗和费用（DeepSeek 官方定价） |

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端 | Python 3.12 + Flask + psycopg2 |
| 数据库 | PostgreSQL 16（Docker 容器） |
| AI 模型 | DeepSeek V4 Flash（OpenAI SDK 兼容，1M 上下文） |
| 前端 | 原生 HTML/CSS/JS，支持暗色/亮色主题 |
| 部署 | Docker Compose（postgres + app 双容器） |

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/wan0791/jxemall-collector.git
cd jxemall-collector

# 2. 配置 DeepSeek API Key
echo "DEEPSEEK_API_KEY=sk-your-key" > .env

# 3. 启动服务
docker compose up -d

# 4. 访问 http://<服务器IP>:5051
# 默认管理员账号: admin / admin123
```

### 本地开发

```bash
# 先启动 PostgreSQL
docker run -d --name pg-jxemall \
  -e POSTGRES_DB=jxemall \
  -e POSTGRES_USER=collector \
  -e POSTGRES_PASSWORD=collector123 \
  -p 5432:5432 postgres:16-alpine

# 安装依赖
pip install -r requirements.txt

# 启动（默认端口 5050）
python app.py
```

## 使用流程

```
手动采集 → AI 分类 → 浏览筛选 → 标记关注 → 导出 Excel
```

1. 登录 Dashboard，点击「手动采集」，选择日期和城市，开始采集
2. 采集完成后，点击「AI 分类」，DeepSeek 自动识别电脑商品
3. 电脑商品自动标注为 ★ 关注，Dashboard 默认展示已关注数据
4. 切换到「全部」可查看所有商品，支持搜索、筛选、行内编辑
5. 导出为 Excel，包含公告原文超链接

## 目标城市

南昌、九江、上饶、鹰潭、景德镇、宜春、萍乡，共 7 城约 75 个区县。

## AI 分类原理

```
待分类记录 → 30条/批打包 → 10并发调 DeepSeek V4 Flash → 写回 is_computer + category + brand
```

- **批量模式**：30 条商品打包为一次 API 请求，利用 1M 上下文窗口
- **并发控制**：10 线程并发，远低于 DeepSeek 500 并发上限
- **自动跳过**：已分类数据不重复消耗 token（match_keywords 标记）
- **Token 统计**：记录每次分类的输入/输出 token，累计费用按 `$0.14/M in + $0.28/M out × 7.25 CNY/USD` 计算
- 支持按日期范围和城市限定分类范围，支持强制重新分类

## Token 消耗参考

| 数据量 | API 调用 | Token | 费用（CNY） |
|--------|---------|-------|-------------|
| ~300 条（1 城市 1 天） | ~10 次 | ~33K | ¥0.05 |
| ~6,000 条（7 城市 1 天） | ~200 次 | ~711K | ¥1.10 |

> DeepSeek V4 Flash 定价极低，百万条记录也仅需几元。

## 项目结构

```
├── app.py               # Flask Web Dashboard（启动入口）
├── collector.py          # API 采集 + content HTML 解析（自定义 SSL 适配器）
├── classifier.py         # DeepSeek V4 Flash 批量分类器
├── db.py                 # PostgreSQL CRUD（psycopg2 连接池）
├── config.py             # 区县代码、DB连接、筛选规则
├── exporter.py           # Excel 导出（openpyxl，按城市分 Sheet）
├── lock.py               # 任务互斥锁
├── Dockerfile            # 应用镜像（Python 3.12-slim）
├── docker-compose.yml    # postgres + app 双容器编排
├── deploy.sh             # 一键部署脚本
├── requirements.txt      # Python 依赖
├── .env.example          # 环境变量模板
├── templates/
│   ├── index.html        # Dashboard 主界面
│   └── login.html        # 登录页
└── data/                 # 运行时生成（task.lock）
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_HOST` | `127.0.0.1` | PostgreSQL 地址 |
| `DB_NAME` | `jxemall` | 数据库名 |
| `DB_USER` | `collector` | 用户名 |
| `DB_PASSWORD` | `collector123` | 密码 |
| `DB_PORT` | `5432` | 端口 |
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 端点 |
| `SECRET_KEY` | （内置默认） | Flask session 密钥 |
| `PORT` | `5050` | 应用端口 |

## 数据库表

| 表名 | 说明 |
|------|------|
| `contracts` | 合同公告数据（serial_num + item_name 组合唯一） |
| `fetch_log` | 采集日志 |
| `classify_log` | AI 分类日志（含 token 统计） |
| `users` | 系统用户 |
| `audit_log` | 操作审计日志 |

## 已知限制

- 分类结果依赖 DeepSeek 模型判断，偶有边界 case 误判
- 最高限价、信创标识（XC）、是否集采字段无法从 API 获取
- 仅支持手动触发采集，无定时任务
- jxemall.com 需 UnsafeLegacyRenegotiation SSL 适配（jxemall 服务器 SSL 配置较旧）

## License

MIT
