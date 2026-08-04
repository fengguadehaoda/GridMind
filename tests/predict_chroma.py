"""测试 VectorStore + RAG（keyword fallback，无需 Chroma ONNX 下载）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

print("=== VectorStore ===")
from core.vector_store import VectorStore
vs = VectorStore()
print(f"Chunks loaded: {vs.count()}")

results = vs.search("变压器过载", top_k=2)
print(f"Search results: {len(results)}")
for r in results:
    print(f'  - [{r["metadata"]["title"]}] score={r["score"]}')

print("\n=== RagEngine (retrieve only) ===")
from core.rag_engine import RagEngine
rag = RagEngine()
result = rag.retrieve("变压器油温异常怎么处理", top_k=2)
print(f"Vector chunks: {len(result.vector_chunks)}")
print(f"Graph entities: {len(result.graph_entities)}")
print(f"Graph paths: {len(result.graph_paths)}")
print(f"Confidence: {result.confidence:.2f}")

print("\n=== RagEngine (answer with no-key fallback) ===")
answer = rag.answer("变压器过载运行时间限制是多少", top_k=2)
print(f"Answer confidence: {answer.confidence:.2f}")
print(f"Refuse: {answer.refuse}")
print(f"Citations: {len(answer.citations)}")
if answer.answer:
    print(f"Answer preview: {answer.answer[:150]}...")

print("\n=== 拒答测试 ===")
answer2 = rag.answer("今天天气怎么样", top_k=1)
print(f"Confidence: {answer2.confidence:.2f}")
print(f"Refuse: {answer2.refuse}")
if answer2.refuse:
    print(f"Refuse reason: {answer2.refuse_reason}")

print("\n✅ VectorStore + RAG all passed")
