#!/usr/bin/env python3
"""OpenAsk 问答客户端：向本地服务发起问题并打印回答。"""

import sys
import requests

API_URL = "http://localhost:8000/api/chat"


def ask(question: str, top_k: int = 3) -> None:
    """向 OpenAsk 服务提问并打印回答。"""
    payload = {"query": question, "top_k": top_k}

    try:
        resp = requests.post(API_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        print(f"\n📝 问题: {question}")
        print(f"{'─' * 50}")
        print(f"🤖 回答:\n{data['answer']}")
        print(f"{'─' * 50}")
        print(f"📚 参考来源: {len(data['sources'])} 条")
        for i, src in enumerate(data["sources"], 1):
            print(f"   {i}. [{src['title']}] 相似度: {src['score']}")
        print(f"💡 缓存命中: {data['cache_hit']}")
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接 {API_URL}，请确认服务已启动")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "你们的退货政策是什么？"

    ask(question)