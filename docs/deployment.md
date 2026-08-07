# GridMind · 灵枢电网 部署文档（v1.4.0）

**项目**：GridMind · 灵枢电网 — 多智能体电网 AI 系统
**版本**：v1.4.0
**更新日期**：2026-08-04

---

## 一、环境要求

| 组件 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.13+ | 后端运行时 |
| **Node.js** | 18+ | 前端构建 |
| **SQLite** | 3.40+ | 内嵌数据库（无需额外安装） |
| **Neo4j** | 5.28+（可选） | 知识图谱（沙箱可降级 NetworkX） |
| **Chroma** | 0.5+ | 向量库（pip 自动安装） |
| **Docker** | 24+（可选） | Neo4j 容器化部署 |
| **DashScope API** | - | 阿里云通义千问 LLM |

---

## 二、生产部署（Docker Compose 推荐）

### 2.1 目录结构

```
/opt/gridmind/
├── docker-compose.yml          # 编排文件
├── .env                         # 环境变量（含 API Key）
├── gridmind-api/                # 后端代码
├── gridmind-web/                # 前端构建产物
├── gridmind-data/               # 持久化数据
└── gridmind-logs/               # 日志
```

### 2.2 docker-compose.yml

```yaml
version: "3.8"

services:
  # ── Neo4j 知识图谱（可选，但推荐）──────
  neo4j:
    image: neo4j:5.28.4
    container_name: gridmind-neo4j
    restart: unless-stopped
    ports:
      - "7687:7687"   # Bolt
      - "7474:7474"   # HTTP
    environment:
      - NEO4J_AUTH=neo4j/<your-password>
      - NEO4J_dbms_memory_heap_max__size=2G
      - NEO4J_dbms_memory_pagecache_size=1G
    volumes:
      - ./gridmind-data/neo4j:/data
    healthcheck:
      test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:7474 || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5

  # ── FastAPI 后端 ────────────────────
  api:
    build:
      context: ./gridmind-api
      dockerfile: Dockerfile
    container_name: gridmind-api
    restart: unless-stopped
    depends_on:
      neo4j:
        condition: service_healthy
    ports:
      - "9900:9900"   # FastAPI
      - "9901:9901"   # MCP Tools
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - API_PORT=9900
      - API_HOST=0.0.0.0
      - MCP_PORT=9901
      - MCP_HOST=0.0.0.0
      - NEO4J_ENABLED=true
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=<your-password>
      - ADMIN_TOKEN=${ADMIN_TOKEN}
      - LOG_LEVEL=INFO
      - MOCK_ENABLED=false
      - EXPLAINABILITY_ENABLED=true
    volumes:
      - ./gridmind-data/sqlite:/app/data
      - ./gridmind-logs:/app/logs
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9900/docs"]
      interval: 30s
      timeout: 10s
      retries: 3

  # ── 前端（静态资源）────────────────────
  web:
    image: nginx:alpine
    container_name: gridmind-web
    restart: unless-stopped
    ports:
      - "5173:80"
    volumes:
      - ./gridmind-web:/usr/share/nginx/html:ro
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - api
```

### 2.3 Dockerfile（API 服务）

```dockerfile
FROM python:3.13-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 数据目录
RUN mkdir -p /app/data /app/logs

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:9900/docs || exit 1

# 启动命令（生产用 gunicorn 或多个 uvicorn worker）
CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "9900", \
     "--workers", "4", \
     "--log-level", "info", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
```

### 2.4 启动

```bash
# 1. 准备环境变量
cat > .env <<EOF
DASHSCOPE_API_KEY=sk-xxxxxxxx
ADMIN_TOKEN=$(openssl rand -hex 32)
NEO4J_PASSWORD=$(openssl rand -hex 16)
EOF

# 2. 构建并启动
docker-compose up -d

# 3. 验证
curl http://localhost:9900/docs
curl http://localhost:5173

# 4. 初始化数据库（首次部署）
docker exec -it gridmind-api python -m scripts.seed_db

# 5. 初始化 Neo4j 知识图谱（首次部署）
docker exec -it gridmind-api python -c "from core.kg_seed_extractor import SeedExtractor; SeedExtractor().run()"
```

---

## 三、单主机部署（Systemd）

适合无 Docker 环境：

### 3.1 /etc/systemd/system/gridmind-api.service

```ini
[Unit]
Description=GridMind FastAPI Backend
After=network.target

[Service]
Type=simple
User=gridmind
WorkingDirectory=/opt/gridmind/gridmind-api
Environment="PYTHONPATH=/opt/gridmind/gridmind-api"
EnvironmentFile=/opt/gridmind/.env
ExecStart=/usr/bin/python3 -m uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 9900 \
    --workers 4 \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=append:/var/log/gridmind/api.log
StandardError=append:/var/log/gridmind/api_err.log

[Install]
WantedBy=multi-user.target
```

### 3.2 启动

```bash
sudo systemctl daemon-reload
sudo systemctl enable gridmind-api
sudo systemctl start gridmind-api
sudo systemctl status gridmind-api
```

---

## 四、环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DASHSCOPE_API_KEY` | ✅ | - | 阿里云通义千问 API Key |
| `API_PORT` | - | 9900 | FastAPI 端口 |
| `API_HOST` | - | 0.0.0.0 | 监听地址 |
| `MCP_PORT` | - | 9901 | MCP Tool 服务端口 |
| `MCP_HOST` | - | 0.0.0.0 | MCP 监听地址 |
| `DATABASE_PATH` | - | data/gridmind.db | SQLite 路径 |
| `CHROMA_PERSIST_DIR` | - | data/chroma_db | 向量库目录 |
| `LOG_LEVEL` | - | INFO | 日志级别 |
| `MOCK_ENABLED` | - | false | Mock 模式（无 API Key 时可临时开启） |
| `NEO4J_ENABLED` | - | false | 启用 Neo4j（生产建议 true） |
| `NEO4J_URI` | 启用时 | - | Bolt URI（如 `bolt://neo4j:7687`） |
| `NEO4J_USER` | 启用时 | - | 用户名 |
| `NEO4J_PASSWORD` | 启用时 | - | 密码 |
| `ADMIN_TOKEN` | ✅ | gridmind-admin-token | 灰度切流 admin token（**生产必须改成复杂随机值**） |
| `EXPLAINABILITY_ENABLED` | - | true | 可解释性 AI 三层 |
| `AUTO_ROLLBACK_WINDOW_S` | - | 300 | 5min 滚动窗口 |
| `AUTO_ROLLBACK_ERROR_RATE` | - | 0.01 | 1% 错误率触发 |
| `AUTO_ROLLBACK_P95_MS` | - | 200 | P95 > 200ms 触发 |
| `GRAYSCALE_RATIO` | - | 0 | Neo4j 切流比例（0/10/50/100） |

---

## 五、监控

### 5.1 Prometheus 抓取

API 自动暴露 `/metrics` 端点（Prometheus exposition format）：

```yaml
# prometheus.yml
scrape_configs:
  - job_name: gridmind
    static_configs:
      - targets: ['gridmind-api:9900']
    scrape_interval: 15s
```

关键指标：
- `gridmind_rag_request_duration_seconds` (Histogram)
- `gridmind_neo4j_query_duration_seconds` (Histogram)
- `gridmind_kg_template_usage_total` (Counter)
- `gridmind_grayscale_ratio` (Gauge)
- `gridmind_rollback_window_error_rate` (Gauge)
- `gridmind_sync_log_pending` (Gauge)

### 5.2 钉钉告警

设置 `DINGTALK_WEBHOOK_URL` 环境变量：

```bash
export DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
export DINGTALK_ENABLED=true
```

告警触发条件：
- rollback 触发（自动/手动）
- Neo4j P95 > 200ms 连续 5min
- sync_log 失败率 > 5%

---

## 六、备份与恢复

### 6.1 SQLite 备份

```bash
# 热备份
sqlite3 data/gridmind.db ".backup '/backup/gridmind-$(date +%Y%m%d).db'"

# 启用 WAL 自动归档
PRAGMA wal_checkpoint(TRUNCATE);
```

### 6.2 Neo4j 备份

```bash
# 容器内
docker exec gridmind-neo4j neo4j-admin dump \
    --database=neo4j \
    --to=/tmp/neo4j-backup.dump
docker cp gridmind-neo4j:/tmp/neo4j-backup.dump /backup/

# 恢复
docker exec gridmind-neo4j neo4j-admin load \
    --from=/tmp/neo4j-backup.dump \
    --database=neo4j \
    --force
```

### 6.3 Chroma 向量库

直接备份 `data/chroma_db/` 目录即可。

---

## 七、安全清单

- [ ] **`.env` 已加密保存**（含真实 DashScope API Key）
- [ ] **`ADMIN_TOKEN` 已改为强随机值**（非默认 `gridmind-admin-token`）
- [ ] **Neo4j 密码已改强**（首次部署随机生成）
- [ ] **HTTPS 已配置**（Nginx + Let's Encrypt）
- [ ] **API 服务仅监听内网**（如 Nginx 反代对外）
- [ ] **MCP 服务端口不对外开放**（9901 仅内网）
- [ ] **审计日志已启用**（HITL audit_log 保留 3 年）
- [ ] **Prometheus 抓取已配置**
- [ ] **钉钉告警 webhook 已设置**
- [ ] **数据备份策略已配置**（每日 cron）

---

## 八、升级流程

```bash
# 1. 备份
./scripts/backup.sh

# 2. 拉取新版本
git pull origin main

# 3. 更新依赖
pip install -r requirements.txt
cd web && npm install && npm run build

# 4. 数据库迁移（如有）
python -m scripts.migrate

# 5. 重启服务（先灰度切流到 0%）
curl -X POST http://localhost:9900/grayscale/set \
    -H "X-Admin-Token: $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"ratio": 0, "actor": "upgrade"}'

sudo systemctl restart gridmind-api

# 6. 验证
curl http://localhost:9900/docs
curl http://localhost:9900/grayscale/status
```

---

## 九、故障排查

| 症状 | 可能原因 | 排查方法 |
|------|---------|----------|
| API 启动失败 | 端口被占用 | `netstat -ano | grep 9900` |
| Neo4j 连接失败 | URI/凭证错误 | `docker logs gridmind-neo4j` |
| Chroma 加载失败 | 目录权限 | `ls -la data/chroma_db/` |
| `/metrics` 404 | 路径错误 | 应为 `GET /metrics` |
| Grayscale 切流失败 | admin_token 错误 | `echo $ADMIN_TOKEN` |
| sync_log 大量 pending | Neo4j 不可用 | `curl /grayscale/metrics` |

---

## 十、容量参考

| 规模 | 用户数 | Chat 数/天 | 推荐配置 |
|------|--------|-----------|----------|
| 小型 | < 50 | < 500 | 2 vCPU / 4GB / 无 Neo4j |
| 中型 | 50-500 | 500-5000 | 4 vCPU / 8GB / Neo4j 1 节点 |
| 大型 | 500-5000 | 5000-50000 | 8 vCPU / 16GB / Neo4j 3 节点集群 |
| 超大 | > 5000 | > 50000 | 16+ vCPU / 32GB+ / Neo4j Causal Cluster |

---

**部署文档版本**：v1.4.0
**最后更新**：2026-08-04