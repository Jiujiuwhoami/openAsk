/** OpenAsk API JavaScript 示例 — 问答 */
const API_KEY = 'sk_your_api_key_here';
const BASE_URL = 'http://localhost:8000';

// 普通问答
async function chat() {
  const resp = await fetch(`${BASE_URL}/api/chat`, {
    method: 'POST',
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query: '退货政策是什么？', top_k: 5 }),
  });
  const data = await resp.json();
  console.log(`回答: ${data.answer}`);
  console.log(`来源: ${data.sources.length} 篇文档`);
  console.log(`缓存: ${data.cache_hit ? '命中' : '未命中'}`);
}

// 流式问答
async function streamChat() {
  const resp = await fetch(`${BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ query: '退货政策是什么？', top_k: 5 }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (!line.trim().startsWith('data: ')) continue;
      const event = JSON.parse(line.slice(6));
      if (event.event === 'answer_delta') {
        process.stdout.write(event.data);
      } else if (event.event === 'done') {
        console.log('\n[完成]');
      }
    }
  }
}

chat();
// streamChat();