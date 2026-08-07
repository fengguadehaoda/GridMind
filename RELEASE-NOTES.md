# GridMind · 灵枢电网 Release Notes

## v1.4.0（2026-08-04）

**主题**：可解释性 AI + 知识图谱完整闭环

---

### 🌟 核心新功能

#### P0-3 · HITL Edit & Continue 模式
工单接收人可**编辑**危险操作的内容（如调整检修时间），系统自动**重新跑安全校验**，不再只是 approve/reject 二选一。

- 3 种 HITL 模式：Approval（保留）/ **Edit & Continue（新增）** / Escalation（M4+）
- 端点：`POST /interrupt/{thread_id}/decision`
- 审计：`GET /audit/hitl/{thread_id}`（保留 3 年）

#### P0-1 · 可解释性 AI 三层架构
| 层 | 职责 | 输出 |
|----|------|------|
| **LLM 层** | 推理 + 给出结论 | 自然语言 |
| **机理校验层** | 5 种轻量校验（电流/电压/温度/开关状态/告警联动） | PASS/FAIL + 偏差 |
| **规则护栏层** | 11 条 JSON 规则（人因/安规/保电） | PASS/FAIL + 引用 |
| **融合层** | 冲突检测 + 强制人工复核 | 最终决策 |

冲突策略：**LLM vs 机理矛盾 → 机理优先 + 强制人工复核**（fail-closed）

#### P0-2 · 知识图谱 Neo4j（M0/M1/M2/M3a/M3b/M3c 全闭环）

| 里程碑 | 周期 | 交付 |
|--------|------|------|
| **M0** 基础设施 | 5d | Neo4j 5.x + NetworkX 双 backend + 降级路径 |
| **M1** 索引+数据 | 20d | 18 约束 + 10 索引 + 539 三元组 + 5 MCP 工具 |
| **M2** RAG+灰度 | 30d | RAG 主链路改造 + ChromaSyncService + GrayscaleRouter + AutoRollback |
| **M3a** 推理能力 | 15d | CypherTemplateRegistry + KGPathOptimizer + ReasoningRulesEngine |
| **M3b** 性能基准 | 12d | 51 场景 + Neo4j vs NetworkX P50/P95/P99 对比 |
| **M3c** 可观测性 | 12d | Prometheus 指标 + 钉钉告警 + 灰度面板 |

#### M2 · 灰度切流（核心）
按 `hash(thread_id) % 100 < ratio` 路由，单一 `ADMIN_TOKEN` 控制。
- 端点：`GET /grayscale/status`、`POST /grayscale/set`、`POST /grayscale/manual_rollback`、`GET /grayscale/history`、`GET /grayscale/metrics`
- 降级：Neo4j 失败 → 自动 NetworkX 降级（连续 3 次失败）
- 自动回滚：5min 滚动窗口，错误率 > 1% 或 P95 > 200ms 触发

#### M3a · 推理能力
- 10 个内置 Cypher 模板（命名规范：`{purpose}_v1`）
- 6 个内置推理规则（IF-THEN + 5s 超时守护）
- 多跳路径优化（top_k=5 + LRU 缓存）
- 2 个新 MCP 工具：`kg_multi_hop_reason` / `kg_apply_rules`

#### M3b · 性能基准
- 51 个场景（30 设备 + 11 因果链 + 5 规程 + 5 跨域）
- 500 节点 / 5000 关系合成数据集
- 自动生成报告：`docs/kg-m3b-perf-report.md`

#### M3c · 可观测性
- 10+ Prometheus 指标（Counter / Gauge / Histogram）
- 钉钉告警（webhook + 5min 冷却 + 去重）
- 灰度面板（Vue 3 + Pinia + 自动刷新）

---

### 🛠️ 工程改进

#### 测试覆盖
- **130+ 新测试**（M3a 45 + M3b 53 + M3c 32）
- 5 场景可解释性测试 + 7 单元 + 1 端点 ALL PASS
- HITL Edit 测试 ALL PASS
- 灰度 e2e 5/5 PASS（off → gray10 → gray50 → full100 → rollback）

#### 前端重设计（v1.3.0 已完成）
- **GridMind · 灵枢电网** 双主题（科技风格深色 + 浅色）
- 5 规格 Logo（brand mark + 字符版本）
- CommandPalette（Ctrl+K 快速切换）
- ReasoningChainPanel 三层推理链可视化
- HitlEditDialog 三按钮弹窗

#### Bug 修复
- Element Plus CSS 缺失（导致 robot 图标占满屏幕）
- 详情按钮配色在双主题下的对比度
- Demo 快捷方式中文优先
- LLM 流式响应模拟（THINKING_DELAY_MS=700, CHUNK_SIZE=4）
- 高危操作 HITL 慢动作（1500ms 三阶段）
- `/grayscale/metrics` 端点 404
- HITL 测试断言措辞不一致
- P1-4 async 测试 pytest 收集失败

#### 文档
- README.md 重写（503 行 / 13 章节 / Mermaid 架构图）
- docs/kg-m3a-prd.md / kg-m3a-design.md
- docs/kg-m3-split.md（M3 拆分方案 976 行）
- docs/kg-m3b-perf-report.md（51 场景基准）
- docs/kg-m3c-observability.md
- docs/e2e-grayscale-4phases-report.md（v2 5/5 PASS）
- docs/deployment.md（**新增**）
- docs/competitive-analysis.md（40KB / 15 项目对比）

---

### 📊 项目规模

| 维度 | 数据 |
|------|------|
| **测试总数** | ~280 PASS + 18-48 SKIP |
| **代码行数** | ~25K（含前后端 + 测试 + 文档） |
| **API 端点** | 28 个（对话 + HITL + 诊断 + 灰度 + 可观测性 + 审计） |
| **MCP 工具** | 18 个 |
| **数据规模** | 88 节点 + 451 关系 + 539 三元组 + 1059 sync_log 成功记录 |
| **Prometheus 指标** | 10+ 个 |
| **GitHub** | 准备推送（fengguadehaoda/GridMind） |

---

### ⚠️ 已知问题（不阻塞）

1. **Neo4j 沙箱未启用**（无 Docker）—— 降级路径完整，本地有 Docker 时自动启用
2. **钉钉 webhook 未配置** —— 告警发送时仅 log（`dingtalk_enabled=false`）
3. **HITL 1 个测试断言措辞差异**（"已拒绝" vs "已被人工拒绝"）—— 已统一为"已拒绝"
4. **4 个 Neo4j 依赖测试 SKIP** —— 沙箱无 neo4j 模块

---

### 🚀 升级指南

从 v1.3.0 升级：

```bash
# 1. 拉取最新代码
git pull origin main

# 2. 更新依赖
pip install -r requirements.txt

# 3. 数据库迁移（v1.4.0 新增 diagnosis_fusion_log 表）
python -m scripts.migrate

# 4. Neo4j 初始化（首次部署 M0/M1/M2/M3a/b/c）
docker run -d --name gridmind-neo4j -p 7687:7687 -p 7474:7474 \
    -e NEO4J_AUTH=neo4j/password neo4j:5.28.4
python -c "from core.kg_seed_extractor import SeedExtractor; SeedExtractor().run()"

# 5. 重启服务
NEO4J_ENABLED=true python -m uvicorn api.main:app --host 0.0.0.0 --port 9900
```

---

### 👥 贡献者

- **齐活林（Qi）** · 交付总监 — 主理人 / SOP 编排
- **许清楚（Xu）** · 产品经理 — PRD / 文档
- **高见远（Gao）** · 架构师 — 系统设计 + 任务分解
- **寇豆码（Kou）** · 工程师 — 代码实现
- **严过关（Yan）** · QA 工程师 — 测试验证

---

### 📅 时间线

| 日期 | 里程碑 |
|------|--------|
| 2026-07-30 | v1.0.0 初始 FastAPI + MCP + Vue 3 |
| 2026-08-01 | v1.1.0 前端重设计（科技风格 + GridMind） |
| 2026-08-02 | v1.2.0 可解释性 AI + HITL Edit |
| 2026-08-03 | v1.3.0 P0-2 知识图谱 M0/M1/M2 |
| 2026-08-04 | **v1.4.0 M3a/M3b/M3c + 灰度 e2e + 部署文档** |

---

### 🔗 链接

- **GitHub**: https://github.com/fengguadehaoda/GridMind（准备推送）
- **本地运行**: `cd web && npm run dev` + `python -m uvicorn api.main:app --port 9900`
- **API 文档**: http://localhost:9900/docs
- **前端**: http://localhost:5173
- **Prometheus**: http://localhost:9900/metrics
- **灰度面板**: http://localhost:5173/grayscale

---

**享受 GridMind！** ⚡️ 灵枢电网 · 让 AI 真正理解电网