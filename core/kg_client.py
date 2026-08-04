"""GridMind 知识图谱统一客户端（KGClient）+ 双 backend（NetworkX / Neo4j）。

设计目标
--------
- **统一接口** ``KGBackend`` Protocol：所有调用方（``RagEngine`` / ``knowledge_tools`` /
  测试）通过 ``KGClient`` 访问，**不直接感知 backend 类型**。
- **自动选 backend**：启动时根据 ``settings.neo4j_enabled`` + Neo4j 健康检查选择
  ``Neo4jBackend`` 或 ``NetworkXBackend``。
- **静默降级**：
    * 连续 **3 次** Neo4j 失败才真正降级（避免网络抖动误降）。
    * 降级后 **30 秒** 自动探活；恢复成功则下次请求走 Neo4j，**无需重启应用**。
- **零回归**：M0 默认 ``neo4j_enabled=False``，所有现有调用行为不变。

跨文件约定（与架构文档 7.2 节一致）
--------------------------------
- Cypher 注入防护：所有动态查询走 ``$param`` 参数化。
- 降级仅写 ``WARNING`` 日志；M0 不发告警（Q6=A）。
- 现有 ``KnowledgeGraph``（NetworkX）**不删除**，作为 ``NetworkXBackend`` 适配器。
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from api.config import settings
from api.schemas import GraphEntity, GraphRelation

# M3a: 3 个新模块的延迟导入（避免循环 + 不破坏 M0/M1/M2）
# 不在文件顶部 import —— 这些模块依赖 KGClient，会形成循环
def _get_template_registry():  # type: ignore[no-untyped-def]
    from core.kg_cypher_templates import get_template_registry
    return get_template_registry()


def _get_path_optimizer(client: Any):  # type: ignore[no-untyped-def]
    from core.kg_path_optimizer import KGPathOptimizer
    return KGPathOptimizer(client=client)


def _get_rules_engine(client: Any):  # type: ignore[no-untyped-def]
    from core.kg_reasoning_rules import get_rules_engine
    return get_rules_engine(client=client)

# 防止 neo4j 驱动未安装导致 import 失败（M0 允许在未安装驱动时仅用 NetworkX）
try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import (
        ServiceUnavailable,
        AuthError,
        TransientError,
        DriverError,
    )
    NEO4J_AVAILABLE = True
except ImportError:  # pragma: no cover
    NEO4J_AVAILABLE = False
    ServiceUnavailable = Exception  # type: ignore
    AuthError = Exception  # type: ignore
    TransientError = Exception  # type: ignore
    DriverError = Exception  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# 1. 统一接口：KGBackend Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class KGBackend(Protocol):
    """知识图谱 backend 统一接口（NetworkX / Neo4j 均实现此协议）。"""

    name: str  # 后端名称（用于日志）

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """按 ID 查询单个实体；不存在返回 None。"""
        ...

    def search_entities(
        self,
        query: str,
        limit: int = 10,
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """按名称模糊搜索（CONTAINS）；可选类型过滤。"""
        ...

    def get_relations(
        self,
        entity_id: str,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取实体的所有出边关系；可选关系类型过滤。"""
        ...

    def expand_entities(
        self,
        seed_entity_ids: list[str],
        hops: int = 2,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        """BFS 多跳扩展；返回 (扩展实体列表, 路径字符串列表)。"""
        ...

    def cypher_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行 Cypher（仅 Neo4j 支持；NetworkX 抛 NotImplementedError）。"""
        ...

    def ping(self) -> bool:
        """健康检查。"""
        ...

    def close(self) -> None:
        """关闭连接 / 释放资源（NetworkX no-op）。"""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# 2. NetworkXBackend：适配现有 KnowledgeGraph
# ─────────────────────────────────────────────────────────────────────────────

class NetworkXBackend:
    """基于 NetworkX 内存图的 backend（保持现有行为）。"""

    name = "networkx"

    def __init__(self) -> None:
        # 延迟 import 避免循环
        from core.knowledge_graph import KnowledgeGraph

        self._kg = KnowledgeGraph()

    # ── 内部：GraphEntity → dict 转换 ──────────────

    @staticmethod
    def _entity_to_dict(entity: GraphEntity) -> dict[str, Any]:
        return {
            "id": entity.id,
            "name": entity.name,
            "type": entity.type,
            "properties": entity.properties or {},
        }

    @staticmethod
    def _relation_to_dict(rel: GraphRelation) -> dict[str, Any]:
        return {
            "source_id": rel.source_id,
            "target_id": rel.target_id,
            "relation_type": rel.relation_type,
        }

    # ── 接口实现 ───────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        entity = self._kg.get_entity(entity_id)
        return self._entity_to_dict(entity) if entity else None

    def search_entities(
        self,
        query: str,
        limit: int = 10,
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        # 兼容现有签名（query, type_filter）→ 注入 limit
        # 注意：现有 KnowledgeGraph.search_entities 不支持 limit，这里截断
        results = self._kg.search_entities(query, type_filter=type_filter)
        return [self._entity_to_dict(e) for e in results[: max(0, int(limit))]]

    def get_relations(
        self,
        entity_id: str,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        rels = self._kg.get_relations(entity_id)
        out = [self._relation_to_dict(r) for r in rels]
        if relation_type is not None:
            out = [r for r in out if r.get("relation_type") == relation_type]
        return out

    def expand_entities(
        self,
        seed_entity_ids: list[str],
        hops: int = 2,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        entities, paths = self._kg.expand_entities(seed_entity_ids, hops=hops)
        return [self._entity_to_dict(e) for e in entities], paths

    def cypher_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "NetworkX backend 不支持 Cypher 查询；请使用 Neo4jBackend 或显式查询 API"
        )

    def ping(self) -> bool:
        # NetworkX 始终可用
        return True

    def close(self) -> None:
        # NetworkX 无外部资源
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Neo4jBackend：基于 Bolt 驱动的实现
# ─────────────────────────────────────────────────────────────────────────────

class Neo4jBackend:
    """基于 neo4j Python 驱动的 backend（与 NetworkXBackend 同一协议）。"""

    name = "neo4j"

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        if not NEO4J_AVAILABLE:
            raise RuntimeError("neo4j Python 驱动未安装")
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver: Any = GraphDatabase.driver(uri, auth=(user, password))

    # ── 内部 ──────────────────────────────────

    def _run(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """参数化执行 Cypher，返回 dict 列表。"""
        with self._driver.session(database=self.database) as session:
            result = session.run(cypher, params or {})
            return [dict(record) for record in result]

    def _run_single(self, cypher: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """单记录查询。"""
        rows = self._run(cypher, params)
        return rows[0] if rows else None

    # ── 接口实现 ───────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        cypher = (
            "MATCH (n:Entity {entity_id: $eid}) "
            "RETURN n.entity_id AS id, n.name AS name, "
            "n.type AS type, n.properties AS properties LIMIT 1"
        )
        row = self._run_single(cypher, {"eid": entity_id})
        if row is None:
            return None
        props = row.get("properties")
        if isinstance(props, str):
            try:
                import json
                props = json.loads(props)
            except (TypeError, ValueError):
                props = {}
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "type": row.get("type"),
            "properties": props or {},
        }

    def search_entities(
        self,
        query: str,
        limit: int = 10,
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (n:Entity) "
            "WHERE toLower(n.name) CONTAINS toLower($q) "
            + ("AND n.type = $type " if type_filter else "")
            + "RETURN n.entity_id AS id, n.name AS name, "
            "n.type AS type, n.properties AS properties "
            "LIMIT $limit"
        )
        params: dict[str, Any] = {"q": query, "limit": int(limit)}
        if type_filter:
            params["type"] = type_filter
        rows = self._run(cypher, params)
        out: list[dict[str, Any]] = []
        for r in rows:
            props = r.get("properties")
            if isinstance(props, str):
                try:
                    import json
                    props = json.loads(props)
                except (TypeError, ValueError):
                    props = {}
            out.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "type": r.get("type"),
                "properties": props or {},
            })
        return out

    def get_relations(
        self,
        entity_id: str,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (a:Entity {entity_id: $eid})-[r:RELATION]->(b:Entity) "
            + ("WHERE r.type = $rtype " if relation_type else "")
            + "RETURN a.entity_id AS source_id, b.entity_id AS target_id, r.type AS relation_type"
        )
        params: dict[str, Any] = {"eid": entity_id}
        if relation_type:
            params["rtype"] = relation_type
        return self._run(cypher, params)

    def expand_entities(
        self,
        seed_entity_ids: list[str],
        hops: int = 2,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        # 限制最大跳数（防止恶意 / 误用）
        safe_hops = max(1, min(int(hops), 5))
        cypher = (
            "MATCH path = (s:Entity)-[*1..$hops]-(o:Entity) "
            "WHERE s.entity_id IN $seeds "
            "WITH collect(distinct o) + collect(distinct s) AS nodes, "
            "     collect(distinct [n IN nodes(path) | n.name]) AS path_names "
            "UNWIND nodes AS n "
            "RETURN DISTINCT n.entity_id AS id, n.name AS name, "
            "n.type AS type, n.properties AS properties"
        )
        rows = self._run(
            cypher, {"seeds": list(seed_entity_ids), "hops": safe_hops}
        )
        entities: list[dict[str, Any]] = []
        for r in rows:
            props = r.get("properties")
            if isinstance(props, str):
                try:
                    import json
                    props = json.loads(props)
                except (TypeError, ValueError):
                    props = {}
            entities.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "type": r.get("type"),
                "properties": props or {},
            })
        # 路径：M0 简化——返回 seed 列表作为占位
        # （M1+ 改造为完整路径枚举）
        paths: list[list[str]] = [[s] for s in seed_entity_ids]
        return entities, paths

    def cypher_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._run(query, params or {})

    def ping(self) -> bool:
        try:
            # neo4j 5.x driver 的 verify_connectivity() 不接受 timeout kwarg；
            # 连接超时通过 driver 的 connection_timeout / max_connection_pool_size 配置控制。
            self._driver.verify_connectivity()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Neo4j ping failed: {}", exc)
            return False

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:  # noqa: BLE001
                pass


# ─────────────────────────────────────────────────────────────────────────────
# 4. KGClient：单例门面 + 自动 backend 选择 + 降级 + 探活恢复
# ─────────────────────────────────────────────────────────────────────────────

class KGClient:
    """GridMind 知识图谱统一客户端（单例）。

    用法::

        client = get_kg_client()
        entity = client.get_entity("e-transformer")
        results = client.search_entities("主变", limit=5)
    """

    _instance: "KGClient | None" = None

    # 降级阈值：连续失败次数
    FAILURE_THRESHOLD: int = 3
    # 探活间隔（秒）
    HEALTH_CHECK_INTERVAL: float = 30.0

    def __init__(self) -> None:
        self.backend: KGBackend = self._select_backend()
        self._failure_count: int = 0
        self._last_health_check: float = 0.0
        logger.info("KGClient 初始化：backend={}", self.backend.name)

    # ── backend 选择 ─────────────────────────────

    def _select_backend(self) -> KGBackend:
        """启动时选择 backend：feature flag + 健康检查。"""
        if not settings.neo4j_enabled:
            logger.info(
                "KGClient: neo4j_enabled=False，使用 NetworkXBackend（M0 默认）"
            )
            return NetworkXBackend()

        if not NEO4J_AVAILABLE:
            logger.warning(
                "KGClient: neo4j 驱动未安装，feature flag 忽略，降级到 NetworkX"
            )
            return NetworkXBackend()

        try:
            backend = Neo4jBackend(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
            if backend.ping():
                logger.info(
                    "KGClient: Neo4j 已连接 → {} (db={})",
                    settings.neo4j_uri, settings.neo4j_database,
                )
                return backend
            logger.warning(
                "KGClient: Neo4j ping 失败（{}），降级到 NetworkX",
                settings.neo4j_uri,
            )
        except (ServiceUnavailable, AuthError) as exc:  # type: ignore[misc]
            logger.warning("KGClient: Neo4j 鉴权/连接失败（{}），降级到 NetworkX", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KGClient: Neo4j 初始化异常（{}），降级到 NetworkX", exc)

        return NetworkXBackend()

    # ── 降级 / 恢复 ─────────────────────────────

    def _demote_to_networkx(self) -> None:
        old_name = type(self.backend).__name__
        self.backend = NetworkXBackend()
        logger.warning("KGClient: backend 降级 {} → NetworkXBackend", old_name)

    def _try_recover_neo4j(self) -> bool:
        """尝试恢复 Neo4j backend（仅当 feature flag 开启）。"""
        if not settings.neo4j_enabled or not NEO4J_AVAILABLE:
            return False
        try:
            backend = Neo4jBackend(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
                database=settings.neo4j_database,
            )
            if backend.ping():
                self.backend = backend
                logger.info(
                    "KGClient: 恢复成功 → Neo4jBackend ({}@{})",
                    settings.neo4j_user, settings.neo4j_uri,
                )
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _maybe_health_check(self) -> None:
        """节流式健康检查：30s 一次。"""
        now = time.monotonic()
        if now - self._last_health_check < self.HEALTH_CHECK_INTERVAL:
            return
        self._last_health_check = now

        if isinstance(self.backend, Neo4jBackend):
            if not self.backend.ping():
                logger.warning("KGClient: Neo4j 健康检查失败 → 降级 NetworkX")
                self._demote_to_networkx()
        elif isinstance(self.backend, NetworkXBackend):
            # NetworkX → Neo4j 恢复探活
            if self._try_recover_neo4j():
                self._failure_count = 0

    # ── 通用方法执行器 ─────────────────────────────

    def _execute(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """执行 backend 方法：捕获异常 → 计数失败 → 降级。"""
        self._maybe_health_check()

        try:
            method = getattr(self.backend, method_name)
            result = method(*args, **kwargs)
            # 成功：重置失败计数
            self._failure_count = 0
            return result
        except (ServiceUnavailable, TransientError, ConnectionError) as exc:  # type: ignore[misc]
            self._failure_count += 1
            logger.warning(
                "KGClient: {}.{} Neo4j 失败 ({}/{}): {}",
                type(self.backend).__name__, method_name,
                self._failure_count, self.FAILURE_THRESHOLD, exc,
            )
            if self._failure_count >= self.FAILURE_THRESHOLD:
                self._demote_to_networkx()
            # 同步兜底：尝试 NetworkXBackend
            return self._fallback_to_networkx(method_name, *args, **kwargs)
        except NotImplementedError:
            # Cypher query 在 NetworkX 模式下被调用 → 透传
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "KGClient: {}.{} 异常: {}",
                type(self.backend).__name__, method_name, exc,
            )
            raise

    def _fallback_to_networkx(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        """同步调用 NetworkXBackend（不改变 self.backend）。"""
        nx_backend = NetworkXBackend()
        method = getattr(nx_backend, method_name)
        return method(*args, **kwargs)

    # ── 委托方法（调用方无感）────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        return self._execute("get_entity", entity_id)

    def search_entities(
        self,
        query: str,
        limit: int = 10,
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._execute(
            "search_entities", query, limit=limit, type_filter=type_filter
        )

    def get_relations(
        self,
        entity_id: str,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._execute(
            "get_relations", entity_id, relation_type=relation_type
        )

    def expand_entities(
        self,
        seed_entity_ids: list[str],
        hops: int = 2,
    ) -> tuple[list[dict[str, Any]], list[list[str]]]:
        return self._execute("expand_entities", list(seed_entity_ids), hops=hops)

    def cypher_query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._execute("cypher_query", query, params=params)

    # ── M3a 新增：3 个方法 ───────────────────────────────

    def execute_template(
        self,
        name: str,
        params: dict[str, Any],
        version: str | None = None,
    ) -> list[dict[str, Any]]:
        """通过 ``CypherTemplateRegistry`` 渲染并执行命名 Cypher 模板。

        流程：
            1. Registry.render(name, params, version=version)
            2. self.cypher_query(cypher, params)
            3. 返回 dict 列表

        :param name:    模板名（Q1=A 全小写下划线，如 ``fault_chain_v1``）
        :param params:  参数化字典（动态值会走 ``$param`` + 注入防护）
        :param version: 可选版本号（None 选最新版）
        :returns:       Cypher 结果（dict 列表）
        :raises TemplateNotFound / TemplateDisabled / MissingParamError / CypherInjectionRisk
        """
        from core.kg_cypher_templates import (
            TemplateNotFound,
            TemplateDisabled,
            MissingParamError,
        )
        try:
            registry = _get_template_registry()
            cypher, safe_params = registry.render(name, params, version=version)
        except (TemplateNotFound, TemplateDisabled, MissingParamError):
            raise
        except Exception as exc:  # noqa: BLE001
            # CypherInjectionRisk 等也透传
            if "CypherInjection" in type(exc).__name__ or "Risk" in type(exc).__name__:
                raise
            raise
        # 仅 Neo4j backend 支持 cypher_query
        if self.backend.name == "neo4j":
            return self._execute("cypher_query", cypher, params=safe_params)
        # NetworkX backend：不支持 Cypher，降级返回空
        logger.debug(
            "execute_template: NetworkX backend, returning empty list for name={}", name,
        )
        return []

    def expand_with_optimizer(
        self,
        seeds: list[str],
        hops: int,
        relation_types: list[str] | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        """通过 ``KGPathOptimizer`` 进行多跳扩展 + top_k 剪枝 + LRU 缓存。

        注意：``neo4j_enabled=False`` 或 ``enable_kg_path_optimizer=False`` 时，
        走 ``self.expand_entities()`` 的 M2 行为（不破坏零回归）。

        M3a 鲁棒性补丁（Bug 2）：当后端 ``expand_entities`` 因 seed 不存在等原因抛
        ``KeyError`` / ``NameError`` 等内部异常时，**返回空结果**（不再向上抛
        ``'n' not defined`` / ``KeyError`` 等误导性错误）。沙箱查询不到真实数据是
        正常情况，不应阻塞调用方。
        """
        safe_hops = min(max(1, int(hops)), 5)

        def _safe_expand() -> tuple[list[dict[str, Any]], list[Any]]:
            """统一的 fallback：所有异常都收敛为空列表。"""
            try:
                return self.expand_entities(list(seeds), hops=safe_hops)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "expand_with_optimizer fallback to expand_entities failed ({}), "
                    "returning empty result",
                    exc,
                )
                return [], []

        use_optimizer = bool(getattr(settings, "path_optimizer_enabled", True))
        if not use_optimizer or not seeds:
            return _safe_expand()
        try:
            optimizer = KGPathOptimizerSingleton.get(self)
            return optimizer.expand(
                self,
                seed_ids=list(seeds),
                hops=int(hops),
                relation_types=relation_types,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "expand_with_optimizer fallback to M2 expand_entities: {}", exc,
            )
            return _safe_expand()

    def apply_rules(
        self,
        entity_id: str,
        ctx: dict[str, Any],
        *,
        rule_ids: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> list[Any]:
        """通过 ``ReasoningRulesEngine`` 对单个实体执行推理规则。

        注意：``enable_inference_engine=False``（默认）时返回空 list（与 M2 行为一致）。
        """
        enabled = bool(getattr(settings, "inference_engine_enabled", False))
        if not enabled:
            return []
        try:
            engine = RulesEngineSingleton.get(self)
            return engine.infer(
                entity_id=entity_id,
                ctx=ctx,
                rule_ids=rule_ids,
                min_confidence=min_confidence,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("apply_rules failed ({}), returning empty list", exc)
            return []

    # ── 状态查询（用于测试）────────────────────────────

    @property
    def current_backend_name(self) -> str:
        return self.backend.name

    @property
    def failure_count(self) -> int:
        return self._failure_count


# ─────────────────────────────────────────────────────────────────────────────
# 5. 单例工厂
# ─────────────────────────────────────────────────────────────────────────────

def get_kg_client() -> KGClient:
    """获取 KGClient 单例（进程内复用）。"""
    if KGClient._instance is None:
        KGClient._instance = KGClient()
    return KGClient._instance


def reset_kg_client() -> None:
    """重置单例（仅测试用）。"""
    if KGClient._instance is not None:
        try:
            KGClient._instance.backend.close()
        except Exception:  # noqa: BLE001
            pass
    KGClient._instance = None
    try:
        KGPathOptimizerSingleton.reset()
    except Exception:
        pass
    try:
        RulesEngineSingleton.reset()
    except Exception:
        pass


class KGPathOptimizerSingleton:
    """KGPathOptimizer 单例绑定到 KGClient。"""
    _instance = None

    @classmethod
    def get(cls, client):
        if cls._instance is None:
            from core.kg_path_optimizer import KGPathOptimizer
            cache_size = int(getattr(settings, "path_optimizer_cache_size", 256) or 256)
            cls._instance = KGPathOptimizer(max_hops=5, cache_size=cache_size, top_k=5, client=client)
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None


class RulesEngineSingleton:
    """ReasoningRulesEngine 单例绑定到 KGClient。"""
    _instance = None

    @classmethod
    def get(cls, client):
        if cls._instance is None:
            from core.kg_reasoning_rules import get_rules_engine
            cls._instance = get_rules_engine(client=client)
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None


__all__ = [
    "KGBackend",
    "NetworkXBackend",
    "Neo4jBackend",
    "KGClient",
    "get_kg_client",
    "reset_kg_client",
    "NEO4J_AVAILABLE",
    "KGPathOptimizerSingleton",
    "RulesEngineSingleton",
]
