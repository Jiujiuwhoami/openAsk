/**
 * OpenAsk JavaScript SDK
 *
 * 使用:
 *   const OpenAsk = require('./openask');
 *   const client = new OpenAsk({ apiKey: 'sk_xxx' });
 *   const res = await client.chat('退货政策是什么？');
 */

class OpenAsk {
  /**
   * @param {Object} opts
   * @param {string} opts.apiKey - 项目 API Key
   * @param {string} [opts.baseUrl='http://localhost:8000'] - API 地址
   * @param {number} [opts.timeout=30000] - 超时毫秒数
   */
  constructor(opts = {}) {
    this.apiKey = opts.apiKey;
    this.baseUrl = (opts.baseUrl || 'http://localhost:8000').replace(/\/+$/, '');
    this.timeout = opts.timeout || 30000;
  }

  async _request(method, path, body = null) {
    const url = `${this.baseUrl}${path}`;
    const headers = { 'X-API-Key': this.apiKey };

    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const resp = await fetch(url, {
        method,
        headers,
        body,
        signal: controller.signal,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      return resp.json();
    } finally {
      clearTimeout(timer);
    }
  }

  /** 问答 */
  async chat(query, top_k = 5) {
    return this._request('POST', '/api/chat', { query, top_k });
  }

  /** 搜索 */
  async search(query, top_k = 10) {
    return this._request('POST', '/api/search', { query, top_k });
  }

  /** 创建文档 */
  async createDocument(title, content, tags = []) {
    return this._request('POST', '/api/knowledge', { title, content, tags });
  }

  /** 列出文档 */
  async listDocuments(page = 1, pageSize = 10) {
    return this._request('GET', `/api/knowledge?page=${page}&page_size=${pageSize}`);
  }

  /** 获取文档 */
  async getDocument(docId) {
    return this._request('GET', `/api/knowledge/${docId}`);
  }

  /** 更新文档 */
  async updateDocument(docId, data) {
    return this._request('PUT', `/api/knowledge/${docId}`, data);
  }

  /** 删除文档 */
  async deleteDocument(docId) {
    return this._request('DELETE', `/api/knowledge/${docId}`);
  }

  /** 健康检查 */
  async health() {
    return this._request('GET', '/api/health');
  }
}

module.exports = { OpenAsk };