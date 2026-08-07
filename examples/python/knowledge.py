"""OpenAsk API Python 示例 — 知识库管理"""
import requests

API_KEY = "sk_your_api_key_here"
BASE_URL = "http://localhost:8000"
headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

# 创建文档
doc = requests.post(
    f"{BASE_URL}/api/knowledge",
    headers=headers,
    json={
        "title": "退货政策",
        "content": "本店支持7天无理由退货，退货需保证商品完好。",
        "tags": ["退货", "政策"],
    },
).json()
doc_id = doc["doc_id"]
print(f"创建文档: {doc['title']} ({doc_id})")

# 列出文档
docs = requests.get(
    f"{BASE_URL}/api/knowledge?page=1&page_size=10",
    headers=headers,
).json()
print(f"文档列表: 共 {docs['total']} 篇")

# 搜索
results = requests.post(
    f"{BASE_URL}/api/search",
    headers=headers,
    json={"query": "退货", "top_k": 5},
).json()
print(f"搜索结果: {len(results)} 条")
for r in results:
    print(f"  - {r['title']} (score: {r['score']:.4f})")

# 更新文档
requests.put(
    f"{BASE_URL}/api/knowledge/{doc_id}",
    headers=headers,
    json={"title": "退货政策（更新版）"},
)
print(f"更新文档: {doc_id}")

# 删除文档
requests.delete(f"{BASE_URL}/api/knowledge/{doc_id}", headers=headers)
print(f"删除文档: {doc_id}")