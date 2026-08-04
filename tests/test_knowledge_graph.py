"""测试 3: 知识图谱 — 加载、搜索、扩展"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.knowledge_graph import KnowledgeGraph

errors = []

# 1. 从数据库加载
try:
    kg = KnowledgeGraph(load_on_init=True)
    # 网络图应该有节点和边
    assert kg.graph.number_of_nodes() > 0, "图没有节点"
    assert kg.graph.number_of_edges() > 0, "图没有边"
    print(f"[PASS] KnowledgeGraph 加载: {kg.graph.number_of_nodes()} 节点, {kg.graph.number_of_edges()} 边")
except Exception as e:
    errors.append(f"加载失败: {e}")
    print(f"[FAIL] 加载: {e}")
    kg = KnowledgeGraph(load_on_init=False)

# 2. 获取实体
try:
    entity = kg.get_entity("e-transformer")
    assert entity is not None, "e-transformer 实体不存在"
    assert entity.name == "变压器"
    assert entity.type == "设备类别"
    print(f"[PASS] get_entity: {entity.name} ({entity.type})")
except Exception as e:
    errors.append(f"get_entity 失败: {e}")
    print(f"[FAIL] get_entity: {e}")

# 3. 搜索实体
try:
    results = kg.search_entities("变压器")
    assert len(results) > 0, "未找到 '变压器' 相关实体"
    names = [r.name for r in results]
    print(f"[PASS] search_entities('变压器'): {names}")
except Exception as e:
    errors.append(f"search_entities 失败: {e}")
    print(f"[FAIL] search_entities: {e}")

# 4. 获取关系
try:
    relations = kg.get_relations("e-transformer")
    assert len(relations) > 0, "e-transformer 没有出边"
    types = [r.relation_type for r in relations]
    targets = [r.target_id for r in relations]
    print(f"[PASS] get_relations(e-transformer): {len(relations)} 条, 目标={targets}")
except Exception as e:
    errors.append(f"get_relations 失败: {e}")
    print(f"[FAIL] get_relations: {e}")

# 5. 多跳扩展
try:
    entities, paths = kg.expand_entities(["e-transformer"], hops=2)
    assert len(entities) > 1, f"扩展后应 >1 个实体, 实际 {len(entities)}"
    print(f"[PASS] expand_entities('e-transformer', hops=2): {len(entities)} 实体, {len(paths)} 路径")
    if paths:
        print(f"       示例路径: {' → '.join(paths[0])}")
except Exception as e:
    errors.append(f"expand_entities 失败: {e}")
    print(f"[FAIL] expand_entities: {e}")

# 6. 跨跳路径 — 一号主变 → 过载 → 处置
try:
    entities, paths = kg.expand_entities(["e-TR001"], hops=2)
    # e-TR001 → e-overload → e-derating/e-shutdown
    assert len(entities) >= 3, f"期望 >=3 实体, 实际 {len(entities)}"
    entity_names = [e.name for e in entities]
    print(f"[PASS] 跨跳路径 e-TR001: {entity_names}")
    if paths:
        print(f"       路径: {' → '.join(paths[0])}")
except Exception as e:
    errors.append(f"跨跳扩展失败: {e}")
    print(f"[FAIL] 跨跳扩展: {e}")

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ 知识图谱全部通过")
