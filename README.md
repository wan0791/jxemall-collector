# 江西电子卖场数据采集系统

从[江西省政府采购电子卖场](https://www.jxemall.com/luban/gongshi)的合同公告中采集电脑销售数据，通过 DeepSeek V4 Flash 大模型智能筛选台式机、笔记本、一体机、组装机等电脑硬件采购合同。

## 功能

- **手动采集**：按日期范围和城市，从 jxemall API 抓取合同公告数据
- **AI 分类**：DeepSeek V4 Flash 大模型批量判断商品是否属于电脑硬件（准确率 >95%）
- **Web Dashboard**：数据浏览、筛选、行内编辑、标记关注、批量操作
- **Excel 导出**：按模板格式导出，含公告原文超链接
- **Token 统计**：累计展示 AI 分类的 token 消耗和费用

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端 | Python Flask + psycopg2 |
| 数据库 | PostgreSQL 16 (Docker) |
| AI 模型 | DeepSeek V4 Flash（OpenAI SDK 兼容） |
| 前端 | 原生 HTML/CSS/JS（暗色主题） |
| 部署 | Docker Compose（postgres + app 双容器） |

## 快速开始

### Docker 部署

```bash
# 1. 配置 API Key
echo "DEEPSEEK_API_KEY=sk-your-key" > .env

# 2. 启动
docker compose up -d

# 3. 访问 http://<ip>:5050
# 默认账号: admin / admin123
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

# 启动
python app.py
```

## 使用流程

1. 登录 Dashboard → 点击「手动采集」→ 选择日期范围和目标城市 → 开始采集
2. 采集完成后 → 点击「AI 分类」→ 选择日期范围 → DeepSeek 自动筛选电脑商品
3. 筛选结果可导出 Excel，包含公告原文超链接

## AI 分类原理

- 每 30 条商品打包为一次 API 请求（利用 1M 上下文窗口）
- 10 并发线程调用 DeepSeek V4 Flash
- 已分类的数据自动跳过，不重复消耗 token
- 支持强制重新分类（勾选后忽略历史结果）

## Token 消耗参考

| 数据量 | API 调用 | Token | 费用 |
|--------|---------|-------|------|
| 276 条（江西省本级 1 天） | 10 次 | ~33K | ¥0.05 |
| 5,876 条（7 城市 1 天） | 198 次 | ~711K | ¥1.10 |

## 目标城市

南昌、九江、上饶、鹰潭、景德镇、宜春、萍乡 + 江西省本级，覆盖全部区县。

## 已知限制

- 分类结果依赖 DeepSeek 模型判断，偶有边界 case 误判
- 最高限价、信创标识(XC)、是否集采字段无法从 API 获取
- 仅支持手动采集，无定时任务
