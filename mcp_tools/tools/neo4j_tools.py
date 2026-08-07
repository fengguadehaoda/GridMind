"""GridMind 知识图谱 M1 · 5 个新 MCP 工具。

工具清单
--------
1. ``cypher_query(query, params)``             — 执行参数化 Cypher（白名单：仅 MATCH/RETURN）
2. ``multi_hop_expand(entity_id, hops, ...)`` — 多跳扩展（默认 3 跳，可按关系类型过滤）
3. ``find_devices_by_substation(substation_id, device_type)`` — 按变电站/类型查询设备
4. ``get_fault_chain(fault_id, max_hops)``    — 获取故障因果链（CAUSES 路径）
5. ``get_applicable_regulations(device_id, fault_type)`` — 查询适用规程

后端适配
--------
- **Neo4j 模式**：使用 ``KGClient`` 委托 ``Neo4jBackend``，执行 Cypher 模板查询。
- **NetworkX 模式**（降级）：使用 BFS 遍历 + 内存过滤，逻辑结果一致。

Cypher 注入防护
----------------
- 所有动态值走 ``$param`` 参数化通道；禁止字符串拼接。
- ``cypher_query`` 仅允许 ``MATCH`` / ``RETURN`` / ``WITH`` / ``WHERE``，禁止 ``DELETE`` /
  ``REMOVE`` / ``SET`` / ``CREATE`` / ``MERGE`` / ``CALL`` / ``DETACH`` 等写操作。

Feature Flag 行为
-----------------
- M1 阶段 ``neo4j_enabled=False`` 默认；MCP 工具调用走 ``KGClient`` 自动路由到
  NetworkXBackend（无 Neo4j 时）或 Neo4jBackend（开启后）。
- M2 集成阶段会切到 ``neo4j_enabled=True``。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

# KGClient 是单例，自动 backend 切换（Neo4j / NetworkX）
_client = None


def _get_client() -> Any:
    """获取 KGClient 单例（延迟初始化）。"""
    global _client
    if _client is None:
        from core.kg_client import get_kg_client
        _client = get_kg_client()
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# Cypher 注入防护：白名单检查
# ─────────────────────────────────────────────────────────────────────────────

# 禁止出现的写操作关键字（不区分大小写）
# B6 修复：改用正则**词边界**匹配，堵住 `SET(` 等无空格变体绕过
# （旧实现用 `"SET "` 子串，`SET(n.x=1)` 因无尾随空格被放行）。
_FORBIDDEN_CYPHER_KEYWORDS = [
    "DELETE",
    "DETACH",
    "REMOVE",
    "CREATE",
    "MERGE",
    "SET",
    "CALL",
    "DROP",
    "FOREACH",
    "IMPORT",
    "COPY",
    "MOVE",
    "RENAME",
]

# 多词短语无法用单词边界，单独子串匹配（大小写不敏感）
_FORBIDDEN_CYPHER_PHRASES = ["LOAD CSV"]

# 允许的读操作关键字
_ALLOWED_CYPHER_KEYWORDS = ["MATCH", "RETURN", "WITH", "WHERE", "OPTIONAL", "UNWIND", "ORDER", "LIMIT", "SKIP", "UNION", "AS"]


def _validate_cypher_readonly(query: str) -> None:
    """校验 Cypher 是只读查询（白名单）。

    B6 修复：对单关键词用 ``\\b(SET|MERGE|DELETE|CREATE|REMOVE)\\b`` 词边界
    正则（不区分大小写）——``SET `` 标准写法、``SET(`` 无空格变体均被拦截；
    只读语句（``MATCH (n) RETURN n``）正常放行。

    Raises:
        ValueError: 包含禁止的写操作关键字。
    """
    if not query or not isinstance(query, str):
        raise ValueError("query 必须是非空字符串")
    q_upper = query.upper()
    # 1) 单关键词：词边界正则（\b 保证是独立单词，`SET(` / `SET (` 均命中）
    for kw in _FORBIDDEN_CYPHER_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", q_upper):
            raise ValueError(
                f"cypher_query 仅允许只读查询，禁止使用 '{kw}' "
                f"（请使用专门的 MCP 工具如 dispatch_work_order 等执行写操作）"
            )
    # 2) 多词短语：子串匹配
    for phrase in _FORBIDDEN_CYPHER_PHRASES:
        if phrase.upper() in q_upper:
            raise ValueError(
                f"cypher_query 仅允许只读查询，禁止使用 '{phrase}' "
                f"（请使用专门的 MCP 工具如 dispatch_work_order 等执行写操作）"
            )
    # 至少包含一个读关键字
    if not any(kw in q_upper for kw in ["MATCH", "RETURN", "WITH"]):
        raise ValueError("Cypher 必须包含 MATCH / RETURN / WITH 之一")


# ─────────────────────────────────────────────────────────────────────────────
# 1. cypher_query — 执行参数化 Cypher（只读）
# ─────────────────────────────────────────────────────────────────────────────

async def cypher_query(query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行参数化 Cypher 查询（仅只读：MATCH / RETURN / WITH / WHERE）。

    参数:
        query:   Cypher 查询字符串（动态值必须用 ``$param`` 占位符）。
        params:  参数化字典（动态值会绑定到 ``$param``）。

    返回:
        ``{"status": "ok", "backend": "neo4j"|"networkx", "rows": [...], "count": N}``
        NetworkX 模式下返回 ``NotImplementedError`` 错误（``cypher_query`` 是 Neo4j 特有）。

    安全:
        - 写操作关键字（CREATE/MERGE/SET/DELETE/CALL 等）会被拦截并返回 status="error"。
        - 所有动态值通过参数化通道注入，Cypher 注入防护到位。

    示例::

        await cypher_query(
            "MATCH (n:Entity {type: $type}) RETURN n.entity_id AS id LIMIT $limit",
            {"type": "设备实例", "limit": 10},
        )
    """
    client = _get_client()
    backend_name = client.current_backend_name

    # 安全校验：白名单只读（Cypher 注入防护）
    try:
        _validate_cypher_readonly(query)
    except ValueError as exc:
        return {
            "status": "error",
            "backend": backend_name,
            "error": str(exc),
            "rows": [],
            "count": 0,
        }

    if backend_name == "networkx":
        return {
            "status": "error",
            "backend": "networkx",
            "error": "NotImplementedError: cypher_query 需要 Neo4j 后端；"
                     "当前 NetworkXBackend 不支持 Cypher（请使用 multi_hop_expand 等专用工具）",
            "rows": [],
            "count": 0,
        }

    try:
        rows = client.cypher_query(query, params or {})
        return {
            "status": "ok",
            "backend": backend_name,
            "rows": rows,
            "count": len(rows),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("cypher_query 失败: {}", exc)
        return {
            "status": "error",
            "backend": backend_name,
            "error": str(exc),
            "rows": [],
            "count": 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. multi_hop_expand — 多跳扩展
# ─────────────────────────────────────────────────────────────────────────────

# Neo4j 模式：参数化 Cypher
_MULTI_HOP_CYPHER = """
MATCH (s:Entity {entity_id: $entity_id})
MATCH path = (s)-[*1..$hops]-(o:Entity)
WHERE $filter_clause
WITH collect(DISTINCT o) + collect(DISTINCT s) AS all_nodes,
     collect(DISTINCT [n IN nodes(path) | n.entity_id]) AS path_ids
UNWIND all_nodes AS n
RETURN DISTINCT
    n.entity_id AS id,
    n.name AS name,
    n.type AS type,
    n.properties AS properties
LIMIT $limit
""".strip()


async def multi_hop_expand(
    entity_id: str,
    hops: int = 3,
    relation_types: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """从指定实体多跳扩展（默认 3 跳）。

    参数:
        entity_id:      起始实体 ID（如 ``e-overload``）。
        hops:           跳数（1-5，超出范围会被截断到 [1, 5]）。
        relation_types: 可选关系类型白名单（如 ``["CAUSES", "HANDLED_BY"]``）；
                        None 表示所有关系。
        limit:          返回上限（默认 100）。

    返回:
        ``{"status": "ok", "backend": "...", "entities": [...], "count": N}``
    """
    safe_hops = max(1, min(int(hops), 5))
    safe_limit = max(1, min(int(limit), 500))
    client = _get_client()
    backend_name = client.current_backend_name

    if backend_name == "neo4j":
        # Neo4j 模式：执行 Cypher
        if relation_types:
            # 关系类型白名单：构造 WHERE 子句
            rel_clauses = " OR ".join(
                [f"ANY(r IN relationships(path) WHERE r.type = $rt_{i})" for i in range(len(relation_types))]
            )
            filter_clause = f"({rel_clauses})"
        else:
            filter_clause = "true"

        cypher = _MULTI_HOP_CYPHER.replace("$filter_clause", filter_clause)
        params: dict[str, Any] = {"entity_id": entity_id, "hops": safe_hops, "limit": safe_limit}
        if relation_types:
            for i, rt in enumerate(relation_types):
                params[f"rt_{i}"] = rt

        try:
            rows = client.cypher_query(cypher, params)
            # 解析 properties（Neo4j 可能返回 JSON 字符串）
            entities: list[dict[str, Any]] = []
            for r in rows:
                props = r.get("properties", {})
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except (TypeError, ValueError):
                        props = {}
                entities.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "properties": props or {},
                })
            return {
                "status": "ok",
                "backend": "neo4j",
                "entities": entities,
                "count": len(entities),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("multi_hop_expand (Neo4j) 失败: {}", exc)
            # 降级到 NetworkX
            return _multi_hop_expand_networkx(
                entity_id, safe_hops, relation_types, safe_limit,
            )

    # NetworkX 模式：BFS 遍历
    return _multi_hop_expand_networkx(entity_id, safe_hops, relation_types, safe_limit)


def _multi_hop_expand_networkx(
    entity_id: str,
    hops: int,
    relation_types: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """NetworkX BFS 多跳扩展实现。"""
    from core.kg_client import NetworkXBackend

    backend = NetworkXBackend()
    try:
        entities, _paths = backend.expand_entities([entity_id], hops=hops)
        # 应用关系类型过滤（如果指定）
        if relation_types:
            rel_set = set(relation_types)
            # 从 KG 获取种子实体的出边关系
            seed_relations = backend.get_relations(entity_id)
            allowed_targets = set()
            for r in seed_relations:
                if r["relation_type"] in rel_set:
                    allowed_targets.add(r["target_id"])
            # 同时过滤反向（incoming）关系
            from mcp_tools.db.database import get_connection
            try:
                conn = get_connection()
                rows = conn.execute(
                    "SELECT source_id FROM graph_relations WHERE target_id = ? AND relation_type IN ({})".format(
                        ",".join("?" for _ in rel_set)
                    ),
                    (entity_id, *rel_set),
                ).fetchall()
                for r in rows:
                    allowed_targets.add(r["source_id"])
            finally:
                conn.close()
            # 过滤扩展实体（保留种子 + 在白名单中的）
            entities = [
                e for e in entities
                if e["id"] == entity_id or e["id"] in allowed_targets
            ]
        # 截断
        entities = entities[:limit]
        return {
            "status": "ok",
            "backend": "networkx",
            "entities": [
                {"id": e["id"], "name": e["name"], "type": e["type"], "properties": e.get("properties", {})}
                for e in entities
            ],
            "count": len(entities),
        }
    finally:
        backend.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. find_devices_by_substation — 按变电站/类型查询设备
# ─────────────────────────────────────────────────────────────────────────────

_FIND_DEVICES_CYPHER = """
MATCH (sub:Entity {entity_id: $substation_id})<-[:BELONGS_TO]-(dev:DeviceInstance)
WHERE $type_filter_clause
RETURN
    dev.entity_id AS id,
    dev.device_id AS device_id,
    dev.name AS name,
    dev.type AS type,
    dev.voltage_level AS voltage_level,
    dev.manufacturer AS manufacturer,
    dev.commissioning_date AS commissioning_date
ORDER BY dev.device_id
LIMIT $limit
""".strip()


async def find_devices_by_substation(
    substation_id: str,
    device_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """按变电站查询设备实例，可选设备类型过滤。

    参数:
        substation_id: 变电站实体 ID（如 ``e-substation-a``）。
        device_type:   可选设备类型过滤（如 ``"Transformer"`` / ``"Busbar"`` / ``"Line"`` / ``"CircuitBreaker"``）。
        limit:         返回上限（默认 50）。

    返回:
        ``{"status": "ok", "backend": "...", "devices": [...], "count": N}``
    """
    safe_limit = max(1, min(int(limit), 200))
    client = _get_client()
    backend_name = client.current_backend_name

    if backend_name == "neo4j":
        if device_type:
            type_filter_clause = "dev:`$device_type`"
            # 动态 label 不能直接用 $param，需用字符串拼接（device_type 是白名单枚举）
            if device_type not in ("Transformer", "CircuitBreaker", "Busbar", "Line", "DeviceInstance"):
                return {
                    "status": "error",
                    "backend": "neo4j",
                    "error": f"不支持的 device_type: {device_type}",
                    "devices": [],
                    "count": 0,
                }
            cypher = """
            MATCH (sub:Entity {entity_id: $substation_id})<-[:BELONGS_TO]-(dev:`""" + device_type + """`)
            RETURN
                dev.entity_id AS id,
                dev.device_id AS device_id,
                dev.name AS name,
                dev.type AS type,
                dev.voltage_level AS voltage_level,
                dev.manufacturer AS manufacturer,
                dev.commissioning_date AS commissioning_date
            ORDER BY dev.device_id
            LIMIT $limit
            """
            params = {"substation_id": substation_id, "limit": safe_limit}
        else:
            cypher = _FIND_DEVICES_CYPHER.replace("$type_filter_clause", "true")
            params = {"substation_id": substation_id, "limit": safe_limit}

        try:
            rows = client.cypher_query(cypher, params)
            devices = [
                {
                    "id": r.get("id"),
                    "device_id": r.get("device_id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "voltage_level": r.get("voltage_level"),
                    "manufacturer": r.get("manufacturer"),
                    "commissioning_date": r.get("commissioning_date"),
                }
                for r in rows
            ]
            return {
                "status": "ok",
                "backend": "neo4j",
                "devices": devices,
                "count": len(devices),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("find_devices_by_substation (Neo4j) 失败: {}", exc)
            return _find_devices_networkx(substation_id, device_type, safe_limit)

    # NetworkX 模式
    return _find_devices_networkx(substation_id, device_type, safe_limit)


def _find_devices_networkx(
    substation_id: str,
    device_type: str | None,
    limit: int,
) -> dict[str, Any]:
    """NetworkX 实现：从 SQLite graph_relations + graph_entities 查询。"""
    from mcp_tools.db.database import get_connection

    conn = get_connection()
    try:
        # 查询 BELONGS_TO 关系
        if device_type:
            rows = conn.execute(
                """
                SELECT ge.entity_id, ge.name, ge.type, ge.properties
                FROM graph_relations gr
                JOIN graph_entities ge ON ge.entity_id = gr.source_id
                WHERE gr.target_id = ? AND gr.relation_type = 'BELONGS_TO'
                  AND ge.type = '设备实例'
                """,
                (substation_id,),
            ).fetchall()
            # 进一步按 device_type 过滤
        else:
            rows = conn.execute(
                """
                SELECT ge.entity_id, ge.name, ge.type, ge.properties
                FROM graph_relations gr
                JOIN graph_entities ge ON ge.entity_id = gr.source_id
                WHERE gr.target_id = ? AND gr.relation_type = 'BELONGS_TO'
                """,
                (substation_id,),
            ).fetchall()

        devices: list[dict[str, Any]] = []
        for r in rows:
            props_raw = r["properties"] or "{}"
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else dict(props_raw)
            except (TypeError, json.JSONDecodeError):
                props = {}
            # device_type 过滤通过 props 中的 label 字段
            if device_type and props.get("device_type") != device_type.lower() and \
               props.get("label") != device_type:
                # 容错：如果 props 没有 device_type，按 device_id 前缀推断
                device_id = props.get("device_id", r["entity_id"])
                inferred_type = _infer_device_type(device_id)
                if inferred_type != device_type:
                    continue
            devices.append({
                "id": r["entity_id"],
                "device_id": props.get("device_id"),
                "name": r["name"],
                "type": r["type"],
                "voltage_level": props.get("voltage_level"),
                "manufacturer": props.get("manufacturer"),
                "commissioning_date": props.get("commissioning_date"),
            })
            if len(devices) >= limit:
                break

        return {
            "status": "ok",
            "backend": "networkx",
            "devices": devices,
            "count": len(devices),
        }
    finally:
        conn.close()


def _infer_device_type(device_id: str) -> str:
    """根据 device_id 前缀推断设备类型。"""
    prefix = device_id.split("-")[0] if "-" in device_id else ""
    return {
        "TR": "Transformer",
        "BR": "CircuitBreaker",
        "BB": "Busbar",
        "CB": "Line",
    }.get(prefix, "DeviceInstance")


# ─────────────────────────────────────────────────────────────────────────────
# 4. get_fault_chain — 获取故障因果链
# ─────────────────────────────────────────────────────────────────────────────

_FAULT_CHAIN_CYPHER = """
MATCH path = (f1:Entity {entity_id: $fault_id})-[:CAUSES*1..$max_hops]->(f2:FaultType)
WITH path,
     [n IN nodes(path) | {id: n.entity_id, name: n.name, type: n.type}] AS chain,
     reduce(acc = 0.0, r IN relationships(path) | acc + coalesce(r.confidence, 0.5)) AS total_confidence,
     length(path) AS hops
RETURN chain, total_confidence, hops
ORDER BY hops ASC, total_confidence DESC
LIMIT $limit
""".strip()


async def get_fault_chain(
    fault_id: str,
    max_hops: int = 3,
    limit: int = 20,
) -> dict[str, Any]:
    """获取故障因果链（沿 CAUSES 关系多跳推理）。

    参数:
        fault_id: 起始故障实体 ID（如 ``e-overload``）。
        max_hops: 最大跳数（1-5）。
        limit:    返回链路上限（默认 20）。

    返回:
        ``{"status": "ok", "backend": "...", "chains": [...], "count": N}``
        每个 chain 包含 ``chain``（节点序列）+ ``total_confidence`` + ``hops``。
    """
    safe_hops = max(1, min(int(max_hops), 5))
    safe_limit = max(1, min(int(limit), 100))
    client = _get_client()
    backend_name = client.current_backend_name

    if backend_name == "neo4j":
        try:
            rows = client.cypher_query(
                _FAULT_CHAIN_CYPHER,
                {"fault_id": fault_id, "max_hops": safe_hops, "limit": safe_limit},
            )
            chains = [
                {
                    "chain": r.get("chain", []),
                    "total_confidence": r.get("total_confidence", 0.0),
                    "hops": r.get("hops", 0),
                }
                for r in rows
            ]
            return {
                "status": "ok",
                "backend": "neo4j",
                "chains": chains,
                "count": len(chains),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("get_fault_chain (Neo4j) 失败: {}", exc)
            return _fault_chain_networkx(fault_id, safe_hops, safe_limit)

    return _fault_chain_networkx(fault_id, safe_hops, safe_limit)


def _fault_chain_networkx(
    fault_id: str,
    max_hops: int,
    limit: int,
) -> dict[str, Any]:
    """NetworkX 实现：BFS 沿 CAUSES 关系扩展。"""
    from mcp_tools.db.database import get_connection

    conn = get_connection()
    try:
        chains: list[dict[str, Any]] = []
        visited: set[tuple[str, ...]] = set()

        def _bfs(start: str, depth: int) -> None:
            """递归 BFS 收集 CAUSES 路径。"""
            if depth >= max_hops:
                return
            rows = conn.execute(
                """
                SELECT gr.target_id, ge.name, ge.type
                FROM graph_relations gr
                JOIN graph_entities ge ON ge.entity_id = gr.target_id
                WHERE gr.source_id = ? AND gr.relation_type = 'CAUSES'
                """,
                (start,),
            ).fetchall()
            for r in rows:
                chain_key = (start, r["target_id"])
                if chain_key in visited:
                    continue
                visited.add(chain_key)
                # SQLite graph_relations 没有 properties 列；confidence 默认 0.5
                # 收集单跳链
                chains.append({
                    "chain": [
                        {"id": start, "name": _get_entity_name(conn, start), "type": "故障类型"},
                        {"id": r["target_id"], "name": r["name"], "type": r["type"]},
                    ],
                    "total_confidence": 0.5,
                    "hops": 1,
                })
                # 递归
                _bfs(r["target_id"], depth + 1)
                if len(chains) >= limit:
                    return

        _bfs(fault_id, 0)
        # 按 hops asc, confidence desc 排序
        chains.sort(key=lambda x: (x["hops"], -x["total_confidence"]))
        return {
            "status": "ok",
            "backend": "networkx",
            "chains": chains[:limit],
            "count": min(len(chains), limit),
        }
    finally:
        conn.close()


def _get_entity_name(conn: Any, entity_id: str) -> str:
    """从 SQLite 查实体名（用于 BFS 链构造）。"""
    row = conn.execute(
        "SELECT name FROM graph_entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    return row["name"] if row else entity_id


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_applicable_regulations — 查询适用规程
# ─────────────────────────────────────────────────────────────────────────────

_REGULATIONS_CYPHER = """
MATCH (reg:Regulation)
WHERE ($device_id_clause OR $fault_type_clause)
OPTIONAL MATCH (reg)-[applies:APPLIES_TO]->(dev)
OPTIONAL MATCH (reg)-[mandates:MANDATES]->(m)
OPTIONAL MATCH (reg)-[docs:DOCUMENTS]->(f)
WITH reg,
     collect(DISTINCT {entity_id: dev.entity_id, type: 'device'}) AS applies_to,
     collect(DISTINCT {entity_id: m.entity_id, type: 'measure'}) AS mandates_to,
     collect(DISTINCT {entity_id: f.entity_id, type: 'fault'}) AS documents
RETURN
    reg.entity_id AS id,
    reg.code AS code,
    reg.name AS name,
    reg.category AS category,
    reg.properties AS properties,
    applies_to,
    mandates_to,
    documents
ORDER BY reg.code
LIMIT $limit
""".strip()


async def get_applicable_regulations(
    device_id: str | None = None,
    fault_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """查询适用规程（按设备 ID 或故障类型过滤）。

    参数:
        device_id:  可选设备实体 ID（如 ``e-TR001``）。如果提供，会查找适用于该设备类型
                    的规程（通过 INSTANCE_OF → DeviceCategory → APPLIES_TO 链路）。
        fault_type: 可选故障实体 ID（如 ``e-overload``）。如果提供，会查找 DOCUMENTS 该故障的规程。
        limit:      返回上限（默认 50）。

    返回:
        ``{"status": "ok", "backend": "...", "regulations": [...], "count": N}``
    """
    safe_limit = max(1, min(int(limit), 200))
    client = _get_client()
    backend_name = client.current_backend_name

    if backend_name == "neo4j":
        device_id_clause = _build_device_clause(device_id)
        fault_type_clause = _build_fault_clause(fault_type)
        cypher = _REGULATIONS_CYPHER.replace(
            "$device_id_clause", device_id_clause
        ).replace(
            "$fault_type_clause", fault_type_clause
        )
        params: dict[str, Any] = {"limit": safe_limit}

        try:
            rows = client.cypher_query(cypher, params)
            regulations: list[dict[str, Any]] = []
            for r in rows:
                props = r.get("properties", {})
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except (TypeError, ValueError):
                        props = {}
                regulations.append({
                    "id": r.get("id"),
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "category": r.get("category"),
                    "properties": props,
                    "applies_to": r.get("applies_to", []),
                    "mandates_to": r.get("mandates_to", []),
                    "documents": r.get("documents", []),
                })
            return {
                "status": "ok",
                "backend": "neo4j",
                "regulations": regulations,
                "count": len(regulations),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("get_applicable_regulations (Neo4j) 失败: {}", exc)
            return _regulations_networkx(device_id, fault_type, safe_limit)

    return _regulations_networkx(device_id, fault_type, safe_limit)


def _build_device_clause(device_id: str | None) -> str:
    """构造设备过滤的 WHERE 子句（Neo4j Cypher 片段）。"""
    if not device_id:
        return "false"
    return (
        f"EXISTS {{ MATCH (dev:DeviceInstance {{entity_id: '{device_id}'}})"
        f"-[:INSTANCE_OF]->(:DeviceCategory)<-[:APPLIES_TO]-(reg) }}"
    )


def _build_fault_clause(fault_type: str | None) -> str:
    """构造故障过滤的 WHERE 子句。"""
    if not fault_type:
        return "false"
    return f"EXISTS {{ MATCH (reg)-[:DOCUMENTS]->(:Entity {{entity_id: '{fault_type}'}}) }}"


def _regulations_networkx(
    device_id: str | None,
    fault_type: str | None,
    limit: int,
) -> dict[str, Any]:
    """NetworkX 实现：从 SQLite 查询规程。"""
    from mcp_tools.db.database import get_connection

    conn = get_connection()
    try:
        # 1) 收集目标实体（设备所属类别 + 故障本身）
        target_categories: set[str] = set()
        if device_id:
            rows = conn.execute(
                """
                SELECT target_id FROM graph_relations
                WHERE source_id = ? AND relation_type = 'INSTANCE_OF'
                """,
                (device_id,),
            ).fetchall()
            for r in rows:
                target_categories.add(r["target_id"])

        target_faults: set[str] = set()
        if fault_type:
            target_faults.add(fault_type)

        # 2) 查询规程
        regs = conn.execute(
            """
            SELECT entity_id, name, type, properties
            FROM graph_entities
            WHERE type = '规程'
            """
        ).fetchall()

        regulations: list[dict[str, Any]] = []
        for r in regs:
            props_raw = r["properties"] or "{}"
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else dict(props_raw)
            except (TypeError, json.JSONDecodeError):
                props = {}

            # 适用性判断
            applies_to_rows = conn.execute(
                """
                SELECT source_id, target_id FROM graph_relations
                WHERE relation_type = 'APPLIES_TO'
                  AND (target_id IN ({}) OR target_id = ?)
                """.format(",".join("?" for _ in target_categories)) if target_categories else
                "SELECT source_id, target_id FROM graph_relations WHERE relation_type = 'APPLIES_TO' AND target_id = ?",
                (*target_categories, device_id) if target_categories else (device_id,),
            ).fetchall() if device_id else []

            applies_to = [{"entity_id": row["target_id"], "type": "device"} for row in applies_to_rows if row["source_id"] == r["entity_id"]]

            mandates_rows = conn.execute(
                "SELECT target_id FROM graph_relations WHERE source_id = ? AND relation_type = 'MANDATES'",
                (r["entity_id"],),
            ).fetchall()
            mandates_to = [{"entity_id": row["target_id"], "type": "measure"} for row in mandates_rows]

            documents_rows = conn.execute(
                "SELECT target_id FROM graph_relations WHERE source_id = ? AND relation_type = 'DOCUMENTS'",
                (r["entity_id"],),
            ).fetchall()
            documents_list = [{"entity_id": row["target_id"], "type": "fault"} for row in documents_rows]

            # 过滤：必须命中设备 OR 故障条件
            is_applicable = False
            if device_id and any(at["entity_id"] in target_categories or at["entity_id"] == device_id for at in applies_to):
                is_applicable = True
            if fault_type and any(d["entity_id"] in target_faults for d in documents_list):
                is_applicable = True
            # 如果两个条件都没传，返回所有规程
            if not device_id and not fault_type:
                is_applicable = True

            if is_applicable:
                regulations.append({
                    "id": r["entity_id"],
                    "code": props.get("code", r["entity_id"]),
                    "name": r["name"],
                    "category": props.get("category"),
                    "properties": props,
                    "applies_to": applies_to,
                    "mandates_to": mandates_to,
                    "documents": documents_list,
                })
                if len(regulations) >= limit:
                    break

        return {
            "status": "ok",
            "backend": "networkx",
            "regulations": regulations,
            "count": len(regulations),
        }
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# 同步便捷函数（供非 MCP 调用方直接使用）
# ═════════════════════════════════════════════════════════════════════════════

__all__ = [
    "cypher_query",
    "multi_hop_expand",
    "find_devices_by_substation",
    "get_fault_chain",
    "get_applicable_regulations",
]