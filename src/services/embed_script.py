"""嵌入脚本生成服务。

生成可嵌入第三方网站的一行 JS 代码，用于在电商店铺中展示 AI 聊天组件。

安全特性（SaaS 标准）：
  - 不包含 API Key，只包含 project_id（公开标识）
  - 运行时从 /api/widget/session 换取短期 Token（1h 过期）
  - Token 自动刷新（到期前 5 分钟预刷新）
  - 所有请求使用 X-Widget-Token 头
  - WebSocket 使用 token 参数认证

功能特性：
  - 对话持久化（localStorage + conversation_id，支持多轮上下文）
  - 多语言支持（zh/en）
  - 人工客服转接 + 实时轮询客服消息
  - 超时处理（30s 自动超时，断开时可重试）
  - 清空对话按钮
  - 使用 CSS 类替代内联样式
  - 使用 textContent 替代 innerHTML
  - 支持 api_base 指向 CDN 或后端
  - 支持自定义主题色和初始问候语

实现说明：脚本 JS 使用占位符替换（如 __OPENASK_PID__），避免 f-string 花括号转义的复杂性。
"""

import html as _html
from src.utils.config import settings


def _minify_css(css: str) -> str:
    """去除 CSS 空白和换行。"""
    return " ".join(css.split())


def generate_embed_script(
    project_id: str,
    api_base: str = "",
    primary_color: str = "#409eff",
    title: str = "AI 客服助手",
    greeting: str = "你好！有什么可以帮你的？",
    language: str = "zh",
) -> str:
    """生成嵌入脚本 HTML 代码。

    安全变更（v2.0）：
    - 不再包含 API Key，只含 project_id
    - 页面加载时换取短期 Widget Token
    - 所有请求使用 X-Widget-Token 头

    Args:
        project_id: 项目 ID（公开标识）
        api_base: API 后端地址
        primary_color: 主题色
        title: 聊天窗口标题
        greeting: 初始问候语
        language: 语言（zh/en）

    Returns:
        完整的 HTML 嵌入代码（含 CSS + JS）
    """
    base = api_base or f"http://localhost:{settings.api.port}"

    # HTML 转义所有插入值，防止 XSS
    def esc(v: str) -> str:
        return _html.escape(str(v), quote=True)

    pid = esc(project_id)
    b = esc(base)
    t = esc(title)
    g = esc(greeting)
    lang = esc(language)
    # 主题色用于 CSS，仅作样式值，不做 HTML 转义（只允许 hex 颜色）
    primary = str(primary_color).strip().lstrip("#")
    if not primary or not all(c in "0123456789abcdefABCDEF" for c in primary):
        primary = "409eff"

    css = _minify_css(f"""
        #openask-widget{{all:initial;position:fixed;bottom:20px;right:20px;z-index:999999;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
        #openask-widget *{{box-sizing:border-box}}
        #openask-btn{{width:56px;height:56px;border-radius:50%;background:#{primary};color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,.15);font-size:24px;transition:transform .2s}}
        #openask-btn:hover{{transform:scale(1.1)}}
        #openask-btn.open{{display:none}}
        #openask-panel{{display:none;position:fixed;bottom:90px;right:20px;z-index:999999;width:360px;max-width:calc(100vw - 40px);height:520px;max-height:calc(100vh - 120px);border-radius:12px;background:#fff;box-shadow:0 8px 32px rgba(0,0,0,.12);overflow:hidden;flex-direction:column}}
        #openask-panel.open{{display:flex}}
        #openask-header{{background:#{primary};color:#fff;padding:14px 16px;font-weight:600;display:flex;justify-content:space-between;align-items:center}}
        #openask-close{{cursor:pointer;font-size:16px;padding:0 4px}}
        #openask-msgs{{flex:1;overflow-y:auto;padding:12px;font-size:14px;color:#333;background:#fafafa}}
        #openask-msgs .msg{{margin:8px 0;display:flex}}
        #openask-msgs .msg.user{{justify-content:flex-end}}
        #openask-msgs .msg .bubble{{max-width:80%;padding:8px 12px;border-radius:12px;line-height:1.5;word-break:break-word;white-space:pre-wrap}}
        #openask-msgs .msg.user .bubble{{background:#{primary};color:#fff}}
        #openask-msgs .msg.bot .bubble{{background:#f0f0f0;color:#333}}
        #openask-msgs .msg.bot .bubble.error{{color:#e6a23c;background:#fff3e0}}
        #openask-msgs .msg .retry-btn{{display:inline-block;margin-left:8px;padding:2px 10px;border:1px solid #dcdfe6;border-radius:4px;font-size:12px;color:#409eff;cursor:pointer;background:#fff;line-height:1.5}}
        #openask-msgs .msg .retry-btn:hover{{border-color:#409eff;background:#ecf5ff}}
        #openask-msgs .msg .handoff-btn{{display:inline-block;margin:4px 0 0;padding:4px 12px;border:1px solid #e6a23c;border-radius:4px;font-size:12px;color:#e6a23c;cursor:pointer;background:#fff;line-height:1.5}}
        #openask-msgs .msg .handoff-btn:hover{{border-color:#d4880f;background:#fff7e6}}
        #openask-msgs .msg .handoff-notice .bubble{{border-color:#e6a23c;background:#fffbe6;color:#d4880f}}
        #openask-msgs .greeting{{text-align:center;color:#999;margin-top:160px}}
        #openask-input-row{{display:flex;border-top:1px solid #eee;padding:8px 12px;gap:8px;background:#fff;align-items:center}}
        #openask-clear{{border:none;background:none;cursor:pointer;font-size:16px;padding:0 4px;color:#c0c4cc;line-height:1;transition:color .2s}}
        #openask-clear:hover{{color:#909399}}
        #openask-input{{flex:1;border:1px solid #ddd;border-radius:6px;padding:8px 12px;font-size:14px;outline:none}}
        #openask-input:focus{{border-color:#{primary}}}
        #openask-input:disabled{{background:#f5f7fa;cursor:not-allowed}}
        #openask-send{{padding:8px 16px;border:none;border-radius:6px;background:#{primary};color:#fff;cursor:pointer;font-size:14px;white-space:nowrap}}
        #openask-send:disabled{{opacity:.6;cursor:not-allowed}}
        #openask-handoff{{padding:6px 10px;border:1px solid #{primary};border-radius:6px;background:#fff;color:#{primary};cursor:pointer;font-size:12px;white-space:nowrap;line-height:1}}
        #openask-handoff:hover{{background:#{primary};color:#fff}}
        #openask-handoff:disabled{{opacity:.5;cursor:not-allowed}}
        #openask-powered{{text-align:center;font-size:10px;color:#bbb;padding:4px;background:#fff}}
        #openask-powered a{{color:#bbb;text-decoration:none}}
    """)

    js = JS_TEMPLATE
    for placeholder, value in (
        ("__OPENASK_PID__", pid),
        ("__OPENASK_BASE__", b),
        ("__OPENASK_TITLE__", t),
        ("__OPENASK_GREETING__", g),
        ("__OPENASK_LANG__", lang),
    ):
        js = js.replace(placeholder, value)

    return f"<!-- OpenAsk AI Chat Widget -->\n<style>{css}</style>\n{js}\n<!-- End OpenAsk Widget -->"


# ------------------------------------------------------------------
# JS 模板：使用 __OPENASK_*__ 占位符，避免 f-string 花括号转义
# ------------------------------------------------------------------

JS_TEMPLATE = """<script>
(function() {
var pid='__OPENASK_PID__',base='__OPENASK_BASE__',title='__OPENASK_TITLE__',greeting='__OPENASK_GREETING__',lang='__OPENASK_LANG__';
if(window.__openaskLoaded)return;window.__openaskLoaded=true;
var token=null;
// ---- Token 换取（短期 JWT，不含 API Key） ----
function initSession() {
  fetch(base+'/api/widget/session', {
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({project_id:pid})
  }).then(function(r) {
    if(r.status===403)throw new Error('域名未授权');
    if(!r.ok)throw new Error('HTTP '+r.status);
    return r.json();
  }).then(function(d) {
    token=d.token;
    // 到期前 5 分钟预刷新
    setTimeout(initSession,(d.expires_in-300)*1000);
  }).catch(function(e) {});
}
initSession();
function authHeaders() {var h={'Content-Type':'application/json'};if(token)h['X-Widget-Token']=token;return h;}
var d=document;
function el(tag,cls) {var n=d.createElement(tag);if(cls)n.className=cls;return n;}
function bubble(text,user,cls) {var m=el('div','msg'+(user?' user':' bot'));var b=el('div','bubble'+(cls?' '+cls:''));b.textContent=text;m.appendChild(b);return m;}
// 对话持久化
var LS_KEY='openask_w_'+pid;
var SID_KEY='openask_w_'+pid+'_sid';
function loadHistory() {try {var raw=localStorage.getItem(LS_KEY);return raw?JSON.parse(raw):[];}catch(e) {return[];}}
function saveHistory() {try {var items=[];var nodes=msgs.querySelectorAll('.msg.bot,.msg.user');for(var i=0;i<nodes.length;i++) {var n=nodes[i];items.push({r:n.classList.contains('user')?'u':'b',t:n.querySelector('.bubble').textContent});}localStorage.setItem(LS_KEY,JSON.stringify(items.slice(-50)));}catch(e) {}}
function clearHistory() {try {localStorage.removeItem(LS_KEY);localStorage.removeItem(SID_KEY);}catch(e) {}}
function getSid() {try {return localStorage.getItem(SID_KEY)||'';}catch(e) {return'';}}
function setSid(sid) {try {localStorage.setItem(SID_KEY,sid);}catch(e) {}}
// 容器
var w=el('div');w.id='openask-widget';
var btn=el('div');btn.id='openask-btn';btn.textContent='💬';
var panel=el('div');panel.id='openask-panel';
panel.innerHTML='<div id="openask-header"><span></span><span id="openask-close">✕</span></div><div id="openask-msgs"></div><div id="openask-input-row"><button id="openask-clear" title="清空对话">🗱</button><input id="openask-input" placeholder="输入你的问题…"><button id="openask-handoff" title="转人工客服">转人工</button><button id="openask-send">发送</button></div><div id="openask-powered">Powered by <a href="https://openask.dev" target="_blank" rel="noopener">OpenAsk</a></div>';
w.appendChild(btn);w.appendChild(panel);d.body.appendChild(w);
panel.querySelector('#openask-header span').textContent=title;
var msgs=panel.querySelector('#openask-msgs');
// 恢复历史
var history=loadHistory();
if(history.length===0) {msgs.appendChild(el('div','greeting')).textContent=greeting;}
for(var i=0;i<history.length;i++) {msgs.appendChild(bubble(history[i].t,history[i].r==='u'));}
msgs.scrollTop=msgs.scrollHeight;
var inp=panel.querySelector('#openask-input'),send=panel.querySelector('#openask-send');
var clearBtn=panel.querySelector('#openask-clear'),handoffBtn=panel.querySelector('#openask-handoff');
function open() {btn.classList.add('open');panel.classList.add('open');inp.focus();}
function close() {btn.classList.remove('open');panel.classList.remove('open');}
btn.addEventListener('click',open);
panel.querySelector('#openask-close').addEventListener('click',close);
// 人工客服模式
var agentMode=false,lastMsgId=0,pollTimer=null,wsFail=false,lastQuery='';
clearBtn.addEventListener('click',function() {msgs.innerHTML='';msgs.appendChild(el('div','greeting')).textContent=greeting;clearHistory();agentMode=false;stopPoll();stopWs();});
function addMsg(text,user,cls) {if(cls) {var m=el('div','msg'+(user?' user':' bot'));var b=el('div','bubble'+(cls?' '+cls:''));b.textContent=text;m.appendChild(b);msgs.appendChild(m);}else {msgs.appendChild(bubble(text,user));}saveHistory();msgs.scrollTop=msgs.scrollHeight;}
// 转人工请求
function submitHandoff(reason) {
  var sid=getSid();if(!sid)return;
  handoffBtn.disabled=true;handoffBtn.textContent='提交中…';
  fetch(base+'/api/projects/'+pid+'/handoff', {
    method:'POST',headers:authHeaders(),
    body:JSON.stringify({conversation_id:sid,query:lastQuery||'',contact_email:'',contact_phone:'',note:reason||'',reason:'user_initiated',priority:0})
  })
  .then(function(r) {if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
  .then(function(data) {
    addMsg('已提交转人工请求，等待客服接入…',false,'handoff-notice');
    if(data.status==='agent') {agentMode=true;handoffBtn.style.display='none';addMsg('有客服在线，已为您接入人工客服',false,'handoff-notice');startWs();return;}
    if(data.queue_position>0) {addMsg('当前排队位置：第 '+(data.queue_position+1)+' 位，预计等待约 '+Math.ceil(data.estimated_wait_seconds/60)+' 分钟',false,'handoff-notice');}
    var cancelBtn=document.createElement('span');cancelBtn.className='handoff-btn';cancelBtn.textContent='取消排队';
    cancelBtn.addEventListener('click',function() {
      fetch(base+'/api/projects/'+pid+'/handoff/cancel', {method:'POST',headers:authHeaders(),body:JSON.stringify({conversation_id:sid,query:''})})
      .then(function(r) {return r.json();}).then(function() {addMsg('已取消排队，可继续使用AI服务',false,'handoff-notice');stopWs();}).catch(function() {addMsg('取消失败，请稍后重试',false,'handoff-notice');});
      cancelBtn.remove();
    });
    msgs.lastChild.querySelector('.bubble').after(cancelBtn);
    startWs();
  }).catch(function() {addMsg('提交失败，请稍后重试',false,'handoff-notice');}).finally(function() {handoffBtn.disabled=false;handoffBtn.textContent='转人工';});
}
handoffBtn.addEventListener('click',function() {if(agentMode)return;submitHandoff('');});
// WebSocket
var ws=null,wsFail=false;
function wsUrl() {return base.replace(/^http/,'ws')+'/ws?token='+encodeURIComponent(token||'');}
function startWs() {
  if(!token||wsFail||!window.WebSocket) {startPoll();return;}
  try {ws=new WebSocket(wsUrl());}catch(e) {wsFail=true;startPoll();return;}
  ws.onopen=function() {if(pollTimer)stopPoll();};
  ws.onmessage=function(ev) {var d;try {d=JSON.parse(ev.data);}catch(e) {return;}
    if(d.type==='message.new'&&d.data&&d.data.message) {var m=d.data.message;
      if(m.role==='agent') {if(!agentMode) {agentMode=true;addMsg('已接入人工客服，客服将为您服务',false,'handoff-notice');handoffBtn.style.display='none';}addMsg('客服: '+m.content,false);}
      if(m.role==='system') {addMsg(m.content,false);}
      if(m.role==='user') {if(m.content!==lastQuery)addMsg(m.content,true);}
      if(m.id>lastMsgId)lastMsgId=m.id;
    }
    if(d.type==='message.typing'&&d.data&&d.data.conversation_id) {var cm=msgs.querySelector('#openask-typing');if(!cm) {cm=el('div','msg bot');cm.id='openask-typing';var cb=el('div','bubble');cb.textContent='客服正在输入...';cb.style.fontStyle='italic';cb.style.color='#999';cm.appendChild(cb);msgs.appendChild(cm);msgs.scrollTop=msgs.scrollHeight;}clearTimeout(window._typingTimer);window._typingTimer=setTimeout(function() {if(cm&&cm.parentNode)cm.parentNode.removeChild(cm);},3000);}
    if(d.type==='conversation.status'&&d.data) {if(d.data.status==='agent'&&!agentMode) {agentMode=true;addMsg('已接入人工客服，客服将为您服务',false,'handoff-notice');handoffBtn.style.display='none';}if(d.data.status==='active') {agentMode=false;handoffBtn.style.display='';addMsg('客服已结束会话，您可继续使用AI服务',false,'handoff-notice');showCsat();}}
  };
  ws.onclose=function() {if(!wsFail) {wsFail=true;startPoll();}};
  ws.onerror=function() {try {ws.close();}catch(e) {} };
}
function stopWs() {if(ws) {try {ws.close();}catch(e) {}ws=null;}}
// 轮询
function startPoll() {stopPoll();pollTimer=setInterval(function() {var sid=getSid();if(!sid)return;fetch(base+'/api/chat/poll?conversation_id='+sid+'&since_id='+lastMsgId, {headers:authHeaders()}).then(function(r) {if(!r.ok)throw new Error();return r.json();}).then(function(data) {if(!agentMode&&data.status==='agent') {agentMode=true;addMsg('已接入人工客服，客服将为您服务',false,'handoff-notice');handoffBtn.style.display='none';}if(data.messages&&data.messages.length>0) {for(var i=0;i<data.messages.length;i++) {var m=data.messages[i];if(m.role==='agent')addMsg('客服: '+m.content,false);if(m.role==='system')addMsg(m.content,false);if(m.id>lastMsgId)lastMsgId=m.id;}}}).catch(function() {});},3000);}
function stopPoll() {if(pollTimer) {clearInterval(pollTimer);pollTimer=null;}}
// CSAT 评价
var csatShown=false;
function showCsat() {if(csatShown)return;csatShown=true;var sid=getSid();if(!sid)return;var stars=['','⭐','⭐⭐','⭐⭐⭐','⭐⭐⭐⭐','⭐⭐⭐⭐⭐'];var m=el('div','msg bot');var b=el('div','bubble');b.style.border='1px solid #e6a23c';b.style.background='#fffbe6';b.innerHTML='<div style="font-weight:600;margin-bottom:6px">请为我们的人工服务评分</div><div style="font-size:20px;letter-spacing:2px;cursor:pointer" id="openask-csat">'+stars.join('')+'</div>';m.appendChild(b);msgs.appendChild(m);msgs.scrollTop=msgs.scrollHeight;var csatEl=b.querySelector('#openask-csat');csatEl.addEventListener('click',function(e) {var idx=Array.prototype.indexOf.call(csatEl.children,e.target||csatEl.firstChild)+1;submitCsat(idx);csatEl.innerHTML='感谢您的评价！';});}
function submitCsat(rating) {var sid=getSid();if(!sid)return;fetch(base+'/api/feedback/csat', {method:'POST',headers:authHeaders(),body:JSON.stringify({conversation_id:sid,rating:rating,tags:[],feedback:''})}).catch(function() {});}
// 页面加载时检查已有对话状态
(function() {var sid=getSid();if(!sid)return;fetch(base+'/api/chat/poll?conversation_id='+sid+'&since_id=0', {headers:authHeaders()}).then(function(r) {if(!r.ok)throw new Error();return r.json();}).then(function(data) {if(data.status==='agent') {agentMode=true;handoffBtn.style.display='none';addMsg('已接入人工客服，客服将为您服务',false,'handoff-notice');startWs();}if(data.messages&&data.messages.length>0) {for(var i=0;i<data.messages.length;i++) {var m=data.messages[i];if(m.role==='agent')addMsg('客服: '+m.content,false);if(m.id>lastMsgId)lastMsgId=m.id;}startWs();}}).catch(function() {});})();
var _abort=null;
function sendMsg() {
  var text=inp.value.trim();if(!text||send.disabled)return;
  inp.value='';addMsg(text,true);send.disabled=true;inp.disabled=true;
  lastQuery=text;
  if(agentMode) {var sid=getSid();fetch(base+'/api/chat/message', {method:'POST',headers:authHeaders(),body:JSON.stringify({conversation_id:sid,content:text})}).then(function() {}).catch(function() {addMsg('发送失败，请重试',false,'handoff-notice');}).finally(function() {send.disabled=false;inp.disabled=false;inp.focus();});return;}
  var thinking=el('div','msg bot');var tb=el('div','bubble');tb.textContent='•••';tb.style.fontSize='18px';thinking.appendChild(tb);msgs.appendChild(thinking);msgs.scrollTop=msgs.scrollHeight;
  if(_abort)_abort.abort();
  _abort=new AbortController();
  var timer=setTimeout(function() {if(!_abort.signal.aborted) {_abort.abort();}},30000);
  var sid=getSid();
  var body=JSON.stringify({query:text,top_k:5,conversation_id:sid,language:lang});
  fetch(base+'/api/chat', {method:'POST',headers:authHeaders(),signal:_abort.signal,body:body})
  .then(function(r) {if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
  .then(function(data) {clearTimeout(timer);msgs.removeChild(thinking);addMsg(data.answer||'（无回答）',false);if(data.conversation_id) {setSid(data.conversation_id);}if(data.handoff_suggested) {var hb=document.createElement('span');hb.className='handoff-btn';hb.textContent='未能解决？转人工';hb.addEventListener('click',function() {submitHandoff('AI未能解决，用户主动转接');});msgs.lastChild.querySelector('.bubble').after(hb);}})
  .catch(function(err) {clearTimeout(timer);msgs.removeChild(thinking);if(err.name==='AbortError') {addMsg('请求超时，请重试',false);}else {addMsg('服务暂时不可用，请稍后重试',false);}})
  .finally(function() {send.disabled=false;inp.disabled=false;inp.focus();});
}
send.addEventListener('click',sendMsg);
inp.addEventListener('keydown',function(e) {if(e.key==='Enter')sendMsg();});
})();
</script>"""