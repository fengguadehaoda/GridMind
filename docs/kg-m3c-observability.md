# GridMind M3c 可观测性手册

> 知识图谱 Neo4j · 第 5 里程碑（M0/M1/M2/M3a/M3b 之后）

## 1. 目标

让运维和开发能"看见"M3 系统运行状态：

- **Prometheus 指标**：`GET /metrics` 暴露 13+ 关键指标
- **钉钉告警**：3 类自动告警场景（错误率、P95、连续失败）+ 冷却去重
- **灰度可视化面板**：Vue 组件可视化 `GrayscaleRouter` 状态 / 切换历史 / 监控统计

## 2. 快速接入

### 2.1 启动配置

```bash
# .env
METRICS_ENABLED=true              # Prometheus 端点开关（默认 true）
DINGTALK_ENABLED=false            # 钉钉告警开关（默认 false，webhook 就绪前不要开）
DINGTALK_WEBHOOK_URL=             # 钉钉 webhook URL（空 = sandbox mock log）
DINGTALK_SECRET=                  # 钉钉加签密钥（可选）
DINGTALK_COOLDOWN_S=300           # 同 key 告警冷却（默认 300s = 5min）
GRAYSCALE_PANEL_ENABLED=true      # 前端灰度面板路由开关
```

### 2.2 验证指标可抓取

```bash
# 启动 API（端口 9900）
python -m uvicorn api.main:app --port 9900

# 抓取一次
curl -s http://127.0.0.1:9900/metrics | head -20
```

预期输出：

```
# GridMind M3c metrics (Prometheus exposition format)
# generated_at: 1722700800
# HELP kg_cypher_query_total Total cypher queries by backend ...
# TYPE kg_cypher_query_total counter
# HELP kg_grayscale_ratio Current grayscale ratio percentage ...
# TYPE kg_grayscale_ratio gauge
kg_grayscale_ratio 0
# ...
```

### 2.3 前端灰度面板

浏览器访问 `http://127.0.0.1:5173/grayscale`（Vite 默认 5173）。

页面包含：
- **顶部 4 卡**：当前比例 / 状态机 / 错误率 / 回滚次数
- **手动切流卡**：输入 `X-Admin-Token` + 选择目标比例（0/10/50/100）+ 切流按钮
- **监控窗口卡**：样本数 / 错误率 / P95 / Neo4j 连续失败
- **切换历史卡**：最近 10 次切换 + 触发原因（manual_set / auto_error_rate / auto_p95 / manual）
- **Prometheus 摘要**：JSON 形式的实时指标摘要

自动刷新：`5s` 一次；可点击"立即刷新"按钮强制刷新。

## 3. 指标清单

### 3.1 Counter（单调递增）

| 指标名 | Labels | 含义 |
|--------|--------|------|
| `kg_cypher_query_total` | `backend`, `status` | Cypher 查询次数（backend: neo4j/networkx，status: ok/error） |
| `kg_template_render_total` | `template`, `version` | Cypher 模板渲染次数 |
| `kg_grayscale_switch_total` | `actor`, `from_state`, `to_state` | 灰度切换次数 |
| `kg_rollback_total` | `reason` | 自动回滚次数 |
| `kg_inference_rule_total` | `rule_id`, `outcome` | 推理规则应用次数（M3a） |
| `kg_path_optimizer_cache_total` | `op` | 路径优化器缓存操作（hit/miss/evict） |

### 3.2 Gauge（可增可减）

| 指标名 | 含义 |
|--------|------|
| `kg_grayscale_ratio` | 当前灰度比例（0/10/50/100） |
| `kg_grayscale_state` | 当前状态机数值（off=0/precheck=1/gray10=2/monitoring_24h=3/gray50=4/full100=5/stable=6/rollback=7） |
| `kg_rollback_window_samples` | 当前回滚监控窗口样本数 |
| `kg_rollback_window_error_rate` | 当前回滚监控窗口错误率 |

### 3.3 Histogram（耗时分布）

| 指标名 | Labels | 桶边界（ms） | 含义 |
|--------|--------|--------------|------|
| `kg_cypher_latency_ms` | `backend` | 1/5/10/50/100/200/500/1000 | Cypher 查询延迟 |
| `kg_rag_total_latency_ms` | `backend` | 10/50/100/200/500/1000/2000/5000 | RAG 检索总延迟 |
| `kg_template_render_latency_ms` | `template` | 0.1/0.5/1/5/10/50 | 模板渲染延迟 |

## 4. 告警触发

### 4.1 告警场景

| 场景 | 触发条件 | severity | reason |
|------|----------|----------|--------|
| 错误率高 | 5min 窗口错误率 > `auto_rollback_error_rate`（默认 1%） | critical | `auto_error_rate` |
| P95 超标 | 5min 窗口 P95 延迟 > `auto_rollback_p95_ms`（默认 200ms） | critical | `auto_p95` |
| 连接失败 | Neo4j 连续失败次数 ≥ `auto_rollback_neo4j_fails`（默认 3） | critical | `auto_neo4j_connect` |
| 手动回滚 | 管理员从面板触发 `POST /grayscale/manual_rollback` | warning | `manual` |

### 4.2 告警结构（钉钉 webhook body）

```json
{
  "msgtype": "markdown",
  "markdown": {
    "title": "KG 自动回滚",
    "text": "🚨 **KG 自动回滚**\n\n5min 错误率 1.8% > 1.0%\n\n**严重程度**：critical\n**标签**：\n- **reason**: auto_error_rate\n- **rollback_count**: 3"
  }
}
```

### 4.3 去重策略

- **冷却期**：默认 `5min` 内同 `(title, frozenset(labels))` 只发送一次
- **强制刷新**：调用 `alerter.reset()` 清空去重表（仅测试 / 运维手动用）
- **去重键计算**：`f"{title}:{hash(frozenset(labels.items()))}"`（message 不参与去重）

### 4.4 调用示例

```python
from core.dingtalk_alerter import Alert, get_default_alerter

alerter = get_default_alerter()
ok = alerter.send(Alert(
    title="KG 自动回滚",
    message="5min 错误率 1.8% > 1.0%",
    severity="critical",
    labels={"reason": "auto_error_rate", "rollback_count": "3"},
))
```

## 5. 关键设计

### 5.1 零新增依赖

- **不使用 `prometheus_client`** —— 改用纯标准库实现 Prometheus exposition format
  （避免 `requirements.txt` 增项；本模块输出 100% 兼容 Prometheus 抓取协议）
- **发送 webhook 用 `urllib.request`**（std 库；可选 `httpx`）

### 5.2 Feature flag 隔离

| Flag | 默认 | 关闭时行为 |
|------|------|------------|
| `METRICS_ENABLED` | true | 所有 `record_*` no-op；`/metrics` 返回 404 |
| `DINGTALK_ENABLED` | false | `send()` 直接返回 False；不进入 webhook 发送链 |
| `GRAYSCALE_PANEL_ENABLED` | true | 前端路由保留但不显示；可直接移除 import |

每个钩子都有 `try/except` 容错：metrics / alerter 失败不影响主链路。

### 5.3 单例模式

```python
# 进程内唯一
from core.metrics_collector import get_metrics_collector
from core.dingtalk_alerter import get_default_alerter

metrics = get_metrics_collector()
alerter = get_default_alerter()
```

## 6. 测试覆盖

`tests/test_kg_m3c_*.py` 共 16 个测试，覆盖：

- **MetricsCollector**：Counter / Gauge / Histogram 基础 + record 快捷方法 + export_text 格式 + 单例 + feature flag
- **DingTalkAlerter**：Alert 校验 + sandbox mock + cooldown 去重 + feature flag + 异常处理
- **/metrics 端点**：content-type + 数据流 + 404 + JSON summary + 语法层 promtool 兼容

```bash
pytest tests/test_kg_m3c_*.py -v
```

## 7. 故障排查

| 现象 | 可能原因 | 排错 |
|------|----------|------|
| `/metrics` 返回 404 | `METRICS_ENABLED=false` | 改环境变量 + 重启 |
| 钉钉未发送 | `DINGTALK_ENABLED=false` 或 URL 空 | 检查 `.env` |
| 钉钉发送失败 (http_500) | webhook URL 错误 / 无权限 | 联系钉钉管理员 |
| 同一告警 5min 内只收到一次 | cooldown 去重生效（**正常行为**） | 调小 `DINGTALK_COOLDOWN_S` |
| GrayscalePanel 路由 404 | `GRAYSCALE_PANEL_ENABLED=false` | 改环境变量 + 重启 Vite |
| 历史记录只显示 10 条 | M2 GrayscaleRouter 限制 20 条；面板 slice(0,10) | 调大 `get_history(limit)` |

## 8. 应急回滚

```bash
# 1. 关闭指标采集（业务代码 metrics 钩子 no-op + /metrics 404）
export METRICS_ENABLED=false

# 2. 关闭告警（钉钉 send 直接 False）
export DINGTALK_ENABLED=false
export DINGTALK_WEBHOOK_URL=

# 3. 前端隐藏灰度面板（路由保留但入口下线）
export GRAYSCALE_PANEL_ENABLED=false
# 删除 router/index.ts 中 /grayscale 路由
```

回滚对主链路**零影响**：所有钩子都已 feature flag 隔离。

---

## 9. 相关链接

- 设计文档：`docs/architecture/kg-m3-split.md` §5（M3c 部分）
- 上游模块：
  - M2 `core/grayscale_router.py` —— 灰度切流 + 回滚
  - M3a `core/rag_engine.py` —— 主链路（暴露耗时）
  - M3a `core/kg_cypher_templates.py` —— 模板注册中心（暴露渲染次数）
  - M3a `core/auto_rollback.py` —— 5min 滚动监控（暴露窗口统计）
- 端点：
  - `GET /metrics` —— Prometheus exposition format
  - `GET /metrics/summary` —— JSON 摘要（前端用）
  - `GET /grayscale/status` —— 状态快照（M2 已存在）
  - `POST /grayscale/set` —— 切流（M2 已存在）
  - `POST /grayscale/manual_rollback` —— 手动回滚（M2 已存在）
- 前端：
  - `web/src/views/GrayscalePanel.vue` —— 灰度面板
  - `web/src/api/metrics.ts` —— API 客户端
  - `web/src/stores/metrics.ts` —— Pinia store（5s 自动刷新）
  - 路由：`/grayscale`
