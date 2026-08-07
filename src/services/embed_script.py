"""嵌入脚本生成服务。

生成可嵌入第三方网站的一行 JS 代码，用于在电商店铺中展示 AI 聊天组件。

功能特性：
  - 对话持久化（localStorage + conversation_id，支持多轮上下文）
  - 多语言支持（zh/en）
  - 人工客服转接
  - 超时处理（30s 自动超时，断开时可重试）
  - 清空对话按钮
  - 使用 CSS 类替代内联样式
  - 使用 textContent 替代 innerHTML
  - 支持 api_base 指向 CDN 或后端
  - 支持自定义主题色和初始问候语
"""

import html as _html
from src.utils.config import settings


def _minify_css(css: str) -> str:
    """去除 CSS 空白和换行。"""
    return " ".join(css.split())


def generate_embed_script(
    project_id: str,
    api_key: str = "",
    api_base: str = "",
    primary_color: str = "#409eff",
    title: str = "AI 客服助手",
    greeting: str = "你好！有什么可以帮你的？",
    language: str = "zh",
) -> str:
    """生成嵌入脚本 HTML 代码。

    Args:
        project_id: 项目 ID，编译到脚本中
        api_key: 项目 API Key（sk_ 开头），用于 X-API-Key 鉴权
        api_base: API 地址，默认使用当前服务地址
        primary_color: 主题色（HEX 格式），默认 #409eff
        title: 聊天窗口标题
        greeting: 初始问候语
        language: 回答语言（zh/en）

    Returns:
        完整的 <script> 标签 HTML 代码
    """
    base = api_base or f"http://localhost:{settings.api.port}"

    # 转义嵌入字符串
    pid = _html.escape(project_id, quote=True)
    key = _html.escape(api_key, quote=True)
    b = _html.escape(base, quote=True)
    t = _html.escape(title, quote=True)
    g = _html.escape(greeting, quote=True)
    lang = _html.escape(language, quote=True)

    # 压缩后的 CSS
    css = _minify_css(f"""
        #openask-widget{{all:initial;position:fixed;bottom:20px;right:20px;z-index:999999;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
        #openask-widget *{{box-sizing:border-box}}
        #openask-btn{{width:56px;height:56px;border-radius:50%;background:{primary_color};color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,.15);font-size:24px;transition:transform .2s}}
        #openask-btn:hover{{transform:scale(1.1)}}
        #openask-btn.open{{display:none}}
        #openask-panel{{display:none;position:fixed;bottom:90px;right:20px;z-index:999999;width:360px;max-width:calc(100vw - 40px);height:520px;max-height:calc(100vh - 120px);border-radius:12px;background:#fff;box-shadow:0 8px 32px rgba(0,0,0,.12);overflow:hidden;flex-direction:column}}
        #openask-panel.open{{display:flex}}
        #openask-header{{background:{primary_color};color:#fff;padding:14px 16px;font-weight:600;display:flex;justify-content:space-between;align-items:center}}
        #openask-close{{cursor:pointer;font-size:16px;padding:0 4px}}
        #openask-msgs{{flex:1;overflow-y:auto;padding:12px;font-size:14px;color:#333;background:#fafafa}}
        #openask-msgs .msg{{margin:8px 0;display:flex}}
        #openask-msgs .msg.user{{justify-content:flex-end}}
        #openask-msgs .msg .bubble{{max-width:80%;padding:8px 12px;border-radius:12px;line-height:1.5;word-break:break-word;white-space:pre-wrap}}
        #openask-msgs .msg.user .bubble{{background:{primary_color};color:#fff}}
        #openask-msgs .msg.bot .bubble{{background:#f0f0f0;color:#333}}
        #openask-msgs .msg.bot .bubble.error{{color:#e6a23c;background:#fff3e0}}
        #openask-msgs .msg .retry-btn{{display:inline-block;margin-left:8px;padding:2px 10px;border:1px solid #dcdfe6;border-radius:4px;font-size:12px;color:#409eff;cursor:pointer;background:#fff;line-height:1.5}}
        #openask-msgs .msg .retry-btn:hover{{border-color:#409eff;background:#ecf5ff}}
        #openask-msgs .msg .handoff-btn{{display:inline-block;margin:4px 0 0;padding:4px 12px;border:1px solid #e6a23c;border-radius:4px;font-size:12px;color:#e6a23c;cursor:pointer;background:#fff;line-height:1.5}}
        #openask-msgs .msg .handoff-btn:hover{{border-color:#d4880f;background:#fff7e6}}
        #openask-msgs .greeting{{text-align:center;color:#999;margin-top:160px}}
        #openask-input-row{{display:flex;border-top:1px solid #eee;padding:8px 12px;gap:8px;background:#fff;align-items:center}}
        #openask-clear{{border:none;background:none;cursor:pointer;font-size:16px;padding:0 4px;color:#c0c4cc;line-height:1;transition:color .2s}}
        #openask-clear:hover{{color:#909399}}
        #openask-input{{flex:1;border:1px solid #ddd;border-radius:6px;padding:8px 12px;font-size:14px;outline:none}}
        #openask-input:focus{{border-color:{primary_color}}}
        #openask-input:disabled{{background:#f5f7fa;cursor:not-allowed}}
        #openask-send{{padding:8px 16px;border:none;border-radius:6px;background:{primary_color};color:#fff;cursor:pointer;font-size:14px;white-space:nowrap}}
        #openask-send:disabled{{opacity:.6;cursor:not-allowed}}
        #openask-powered{{text-align:center;font-size:10px;color:#bbb;padding:4px;background:#fff}}
        #openask-powered a{{color:#bbb;text-decoration:none}}
    """)

    return f"""<!-- OpenAsk AI Chat Widget -->
<style>{css}</style>
<script>
(function() {{
var pid='{pid}',key='{key}',base='{b}',title='{t}',greeting='{g}',lang='{lang}';
if(window.__openaskLoaded)return;window.__openaskLoaded=true;
var d=document;
function el(tag,cls){{var n=d.createElement(tag);if(cls)n.className=cls;return n;}}
function bubble(text,user,cls){{var m=el('div','msg'+(user?' user':' bot'));var b=el('div','bubble'+(cls?' '+cls:''));b.textContent=text;m.appendChild(b);return m;}}
// 对话持久化（含 conversation_id）
var LS_KEY='openask_w_'+pid;
var SID_KEY='openask_w_'+pid+'_sid';
function loadHistory(){{try{{var raw=localStorage.getItem(LS_KEY);return raw?JSON.parse(raw):[];}}catch(e){{return[];}}}}
function saveHistory(){{try{{var items=[];var nodes=msgs.querySelectorAll('.msg.bot,.msg.user');for(var i=0;i<nodes.length;i++){{var n=nodes[i];items.push({{r:n.classList.contains('user')?'u':'b',t:n.querySelector('.bubble').textContent}});}}localStorage.setItem(LS_KEY,JSON.stringify(items.slice(-50)));}}catch(e){{}}}}
function clearHistory(){{try{{localStorage.removeItem(LS_KEY);localStorage.removeItem(SID_KEY);}}catch(e){{}}}}
function getSid(){{try{{return localStorage.getItem(SID_KEY)||'';}}catch(e){{return'';}}}}
function setSid(sid){{try{{localStorage.setItem(SID_KEY,sid);}}catch(e){{}}}}
// 容器
var w=el('div');w.id='openask-widget';
var btn=el('div');btn.id='openask-btn';btn.textContent='💬';
var panel=el('div');panel.id='openask-panel';
panel.innerHTML='<div id="openask-header"><span></span><span id="openask-close">✕</span></div><div id="openask-msgs"></div><div id="openask-input-row"><button id="openask-clear" title="清空对话">🗱</button><input id="openask-input" placeholder="输入你的问题…"><button id="openask-send">发送</button></div><div id="openask-powered">Powered by <a href="https://openask.dev" target="_blank" rel="noopener">OpenAsk</a></div>';
w.appendChild(btn);w.appendChild(panel);d.body.appendChild(w);
panel.querySelector('#openask-header span').textContent=title;
var msgs=panel.querySelector('#openask-msgs');
// 恢复历史
var history=loadHistory();
if(history.length===0){{msgs.appendChild(el('div','greeting')).textContent=greeting;}}
for(var i=0;i<history.length;i++){{msgs.appendChild(bubble(history[i].t,history[i].r==='u'));}}
msgs.scrollTop=msgs.scrollHeight;
var inp=panel.querySelector('#openask-input'),send=panel.querySelector('#openask-send');
var clearBtn=panel.querySelector('#openask-clear');
function open(){{btn.classList.add('open');panel.classList.add('open');inp.focus();}}
function close(){{btn.classList.remove('open');panel.classList.remove('open');}}
btn.addEventListener('click',open);
panel.querySelector('#openask-close').addEventListener('click',close);
clearBtn.addEventListener('click',function(){{msgs.innerHTML='';msgs.appendChild(el('div','greeting')).textContent=greeting;clearHistory();}});
function addMsg(text,user){{msgs.appendChild(bubble(text,user));saveHistory();msgs.scrollTop=msgs.scrollHeight;}}
var _abort=null;
function sendMsg(){{
  var text=inp.value.trim();if(!text||send.disabled)return;
  inp.value='';addMsg(text,true);send.disabled=true;inp.disabled=true;
  var thinking=el('div','msg bot');var tb=el('div','bubble');tb.textContent='•••';tb.style.fontSize='18px';thinking.appendChild(tb);msgs.appendChild(thinking);msgs.scrollTop=msgs.scrollHeight;
  if(_abort)_abort.abort();
  _abort=new AbortController();
  var timer=setTimeout(function(){{if(!_abort.signal.aborted){{_abort.abort();}}}},30000);
  var sid=getSid();
  var body=JSON.stringify({{query:text,top_k:5,conversation_id:sid,language:lang}});
  fetch(base+'/api/chat',{{method:'POST',headers:{{'Content-Type':'application/json','X-API-Key':key}},signal:_abort.signal,body:body}})
  .then(function(r){{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}})
  .then(function(data){{
    clearTimeout(timer);msgs.removeChild(thinking);
    addMsg(data.answer||'（无回答）',false);
    // 保存 conversation_id 用于续传
    if(data.conversation_id){{setSid(data.conversation_id);}}
    // 显示转人工按钮
    if(data.handoff_suggested){{var hb=document.createElement('span');hb.className='handoff-btn';hb.textContent='未能解决？转人工';hb.addEventListener('click',function(){{var contact=prompt('请输入联系方式（邮箱或手机号），客服将联系你：');if(contact&&contact.trim()){{fetch(base+'/api/projects/'+pid+'/handoff',{{method:'POST',headers:{{'Content-Type':'application/json','X-API-Key':key}},body:JSON.stringify({{conversation_id:data.conversation_id,query:text,contact_email:contact}})}}).then(function(){{addMsg('转接请求已提交，客服将尽快联系你',false);}}).catch(function(){{addMsg('提交失败，请稍后重试',false);}});}}}});msgs.lastChild.querySelector('.bubble').after(hb);}}
  }})
  .catch(function(err){{
    clearTimeout(timer);msgs.removeChild(thinking);
    if(err.name==='AbortError'){{addMsg('请求超时，请重试',false);}}
    else{{addMsg('服务暂时不可用，请稍后重试',false);}}
    var retry=document.createElement('span');retry.className='retry-btn';retry.textContent='重试';
    retry.addEventListener('click',function(){{inp.value=text;send.disabled=false;inp.disabled=false;sendMsg();}});
    msgs.lastChild.querySelector('.bubble').appendChild(retry);
    msgs.scrollTop=msgs.scrollHeight;
  }})
  .finally(function(){{send.disabled=false;inp.disabled=false;inp.focus();}});
}}
send.addEventListener('click',sendMsg);
inp.addEventListener('keydown',function(e){{if(e.key==='Enter')sendMsg();}});
}})();
</script>
<!-- End OpenAsk Widget -->"""