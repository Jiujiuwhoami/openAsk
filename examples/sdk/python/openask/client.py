"""OpenAsk API 客户端。"""

from typing import Any, Dict, List, Optional

import requests


class OpenAsk:
    """OpenAsk API 客户端。

    Args:
        api_key: 项目 API Key（从项目设置获取）
        base_url: API 地址，默认 http://localhost:8000
        timeout: 请求超时秒数
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("headers", self._headers)
        kwargs.setdefault("timeout", self.timeout)
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ---- 问答 ----

    def chat(
        self,
        query: str,
        top_k: int = 5,
    ) -> Dict:
        """问答接口。

        Args:
            query: 用户问题
            top_k: 返回的参考文档数量

        Returns:
            {"answer": "...", "sources": [...], "cache_hit": bool, "llm_used": bool}
        """
        return self._request("POST", "/api/chat", json={"query": query, "top_k": top_k})

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """搜索接口（仅检索，不生成回答）。"""
        return self._request("POST", "/api/search", json={"query": query, "top_k": top_k})

    # ---- 知识库 ----

    def create_document(self, title: str, content: str, tags: Optional[List[str]] = None) -> Dict:
        """创建文档。"""
        return self._request(
            "POST", "/api/knowledge",
            json={"title": title, "content": content, "tags": tags or []},
        )

    def list_documents(self, page: int = 1, page_size: int = 10) -> Dict:
        """列出文档。"""
        return self._request("GET", f"/api/knowledge?page={page}&page_size={page_size}")

    def get_document(self, doc_id: str) -> Dict:
        """获取文档。"""
        return self._request("GET", f"/api/knowledge/{doc_id}")

    def update_document(self, doc_id: str, **kwargs) -> Dict:
        """更新文档。"""
        return self._request("PUT", f"/api/knowledge/{doc_id}", json=kwargs)

    def delete_document(self, doc_id: str) -> Dict:
        """删除文档。"""
        return self._request("DELETE", f"/api/knowledge/{doc_id}")

    def upload_document(self, file_path: str) -> Dict:
        """上传文档文件。"""
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/api/knowledge/upload",
                headers={"X-API-Key": self.api_key},
                files={"file": f},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()

    # ---- 健康检查 ----

    def health(self) -> Dict:
        """健康检查。"""
        return self._request("GET", "/api/health")