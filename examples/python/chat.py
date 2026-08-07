"""OpenAsk API Python 示例 — 问答"""
import requests

API_KEY = "sk_your_api_key_here"
BASE_URL = "http://localhost:8000"

# 问答
resp = requests.post(
    f"{BASE_URL}/api/chat",
    headers={
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    },
    json={"query": "退货政策是什么？", "top_k": 5},
)
data = resp.json()
print(f"回答: {data['answer'][:100]}...")
print(f"来源: {len(data['sources'])} 篇文档")
print(f"缓存: {'命中' if data['cache_hit'] else '未命中'}")
print(f"LLM: {'使用' if data['llm_used'] else '未使用'}")

# 流式问答
print("\n--- 流式问答 ---")
import json
resp = requests.post(
    f"{BASE_URL}/api/chat/stream",
    headers={
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    },
    json={"query": "退货政策是什么？", "top_k": 5},
    stream=True,
)
for line in resp.iter_lines():
    if line:
        line = line.decode("utf-8")
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event["event"] == "answer_delta":
                print(event["data"], end="", flush=True)
            elif event["event"] == "done":
                print("\n[完成]")
            elif event["event"] == "error":
                print(f"\n[错误] {event['data']}")