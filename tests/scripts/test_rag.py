"""测试 4: 向量库 + RAG 引擎 — 检索、融合、生成"""
import sys, os
# D6：脚本已移至 tests/scripts/，需向上两级到项目根
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.vector_store import VectorStore
from core.rag_engine import RagEngine

errors = []

# 1. 向量库初始化
try:
    vs = VectorStore()
    assert vs.count() == 8, f"期望 8 个知识片段, 实际 {vs.count()}"
    print(f"[PASS] VectorStore: {vs.count()} 个片段加载")
except Exception as e:
    errors.append(f"VectorStore 初始化失败: {e}")
    print(f"[FAIL] VectorStore: {e}")
    vs = None

# 2. 向量检索 (关键词 fallback)
if vs:
    try:
        results = vs.search("变压器过载", top_k=3)
        assert len(results) > 0, "检索返回空"
        assert "content" in results[0], "缺少 content 字段"
        print(f"[PASS] search('变压器过载'): {len(results)} 条结果")
        for r in results:
            title = r.get("metadata", {}).get("title", "N/A")
            print(f"       - [{title}] ({r.get('score', 0):.2f}) {r['content'][:50]}...")
    except Exception as e:
        errors.append(f"search 失败: {e}")
        print(f"[FAIL] search: {e}")

# 3. RAG 引擎初始化
try:
    rag = RagEngine()
    print(f"[PASS] RagEngine 初始化成功")
except Exception as e:
    errors.append(f"RagEngine 初始化失败: {e}")
    print(f"[FAIL] RagEngine: {e}")
    rag = None

# 4. RAG 检索 (不调用 LLM)
if rag:
    try:
        result = rag.retrieve("变压器油温异常应该怎么处理", top_k=3)
        assert len(result.vector_chunks) > 0, "检索未返回向量片段"
        print(f"[PASS] rag.retrieve('变压器油温异常'):")
        print(f"       - 向量片段: {len(result.vector_chunks)}")
        print(f"       - 图谱实体: {len(result.graph_entities)}")
        print(f"       - 图谱路径: {len(result.graph_paths)}")
        print(f"       - 置信度: {result.confidence:.2f}")
    except Exception as e:
        errors.append(f"rag.retrieve 失败: {e}")
        print(f"[FAIL] rag.retrieve: {e}")

# 5. RAG 答案生成 (模版 fallback, 不依赖真实 Key)
if rag:
    try:
        answer = rag.answer("变压器过载运行时间限制是多少?")
        assert answer.refuse is False or answer.confidence >= 0.25
        print(f"[PASS] rag.answer('变压器过载'):")
        print(f"       - 置信度: {answer.confidence:.2f}")
        print(f"       - 拒答: {answer.refuse}")
        print(f"       - 引用: {len(answer.citations)} 个")
        print(f"       - 图谱路径: {len(answer.graph_paths)} 条")
        if answer.answer:
            print(f"       - 回答预览: {answer.answer[:150]}...")
    except Exception as e:
        errors.append(f"rag.answer 失败: {e}")
        print(f"[FAIL] rag.answer: {e}")

# 6. 低置信度拒答
if rag:
    try:
        answer = rag.answer("今天天气怎么样", top_k=1)  # 不相关内容
        print(f"[PASS] rag.answer('今天天气'): 置信度={answer.confidence:.2f}, 拒答={answer.refuse}")
        if answer.refuse:
            print(f"       - 拒答原因: {answer.refuse_reason}")
    except Exception as e:
        errors.append(f"拒答测试失败: {e}")
        print(f"[FAIL] 拒答测试: {e}")

print(f"\n{'='*40}")
if errors:
    print(f"结果: {len(errors)} 个失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("结果: ✅ 向量库 + RAG 全部通过")
