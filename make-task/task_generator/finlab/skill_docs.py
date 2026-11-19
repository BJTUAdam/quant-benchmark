"""Read-only offline API browser for the portable strategy skill."""
from __future__ import annotations

import html
import json
import re

from .common import ROOT, read, textfile


def build_api_browser():
    from .docs import markdown

    base = ROOT / "skills/finlab-strategy/references/tushare"
    snapshot = read(base / "snapshot.json")
    entries = {}
    for item in snapshot["documents"]:
        name = item["api_name"] or "doc-" + item["doc_id"]
        source = (base / item["path"]).read_text(encoding="utf-8")
        article = markdown(source)
        article = re.sub(r'href="\.\./apis/([\w]+)\.md(?:#[^"]*)?"', r'href="#\1"', article)
        article = re.sub(r'href="\.\./guides/(\d+)\.md(?:#[^"]*)?"', r'href="#doc-\1"', article)
        entries[name] = {"name": name, "title": item["title"], "group": item["group"],
                         "status": item.get("content_status", "unknown"),
                         "api": bool(item["api_name"]), "html": article,
                         "search": (name + " " + item["title"] + " " + source).lower()}
    # Escaping the data block prevents source code examples from closing <script>.
    payload = json.dumps(entries, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    groups = sorted({x["group"] for x in entries.values()})
    options = ''.join('<option>' + html.escape(g) + '</option>' for g in groups)
    style = """
*{box-sizing:border-box}body{margin:0;color:#18352f;background:#f3f6f1;font:16px/1.8 system-ui,'Microsoft YaHei',sans-serif}
header{background:#153e35;color:#eff7ef;padding:27px max(22px,4vw)}header h1{margin:0;font-size:29px}header p{margin:6px 0;color:#cce0d2;max-width:950px}
nav{display:flex;gap:20px;margin-top:8px}nav a{color:#d6eee0}main{display:grid;grid-template-columns:320px minmax(0,1fr);max-width:1600px;margin:auto;gap:20px;padding:23px}
aside{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 30px);display:flex;flex-direction:column;min-width:0}
label{font-size:14px;font-weight:650}input,select{display:block;width:100%;margin:6px 0 12px;padding:10px;border:1px solid #b8cdc0;border-radius:7px;background:#fff;font:inherit}
#apiList{overflow:auto;min-height:100px;border:1px solid #ceded1;border-radius:9px;background:#fff}#apiList button{width:100%;border:0;border-bottom:1px solid #e4ece4;text-align:left;background:#fff;padding:11px 14px;color:#223d35;cursor:pointer;font:inherit;overflow-wrap:anywhere}
#apiList button:hover,#apiList button.active{background:#e3f0e6}#apiList small{display:block;color:#617166;font-size:12px}#count{font-size:13px;color:#567064;margin-bottom:8px}
article{background:#fff;border:1px solid #d6e2d7;border-radius:12px;padding:24px 34px;min-width:0;overflow-wrap:anywhere}h2{font-size:25px;line-height:1.45}h3{font-size:21px}h4{font-size:18px}
.notice{background:#fff3df;border-left:4px solid #aa6c22;padding:13px 17px;font-size:14px;margin-bottom:20px}.meta{color:#536b60;font-size:14px}a{color:#156c4e}
.tablewrap{overflow:auto;border:1px solid #dce5dd;border-radius:7px;margin:16px 0}table{border-collapse:collapse;min-width:620px;width:100%;font-size:14px}td,th{text-align:left;border-bottom:1px solid #dce6dc;padding:9px 12px}th{background:#edf4ed}
.codebox{position:relative}pre{background:#152f2b;color:#e4f1e4;padding:24px 20px;overflow:auto;border-radius:8px;font:13px/1.8 Consolas,monospace}.copy{position:absolute;right:8px;top:8px;background:#2a5143;color:#fff;border:1px solid #61826b;border-radius:4px;padding:4px 9px;cursor:pointer}code{font-family:Consolas,monospace}
p code,li code{background:#edf3ec;padding:1px 4px;border-radius:3px}#copyLookup{border:1px solid #9dbba8;border-radius:6px;padding:8px 12px;background:#e6f2e9;color:#1e5f44;cursor:pointer;margin:8px 0 16px}
@media(max-width:800px){main{grid-template-columns:minmax(0,1fr);padding:14px}aside{position:static;max-height:none}#apiList{max-height:220px}article{padding:18px}header h1{font-size:24px}}
"""
    script = """
const entries=JSON.parse(document.getElementById('apiData').textContent);
const list=document.getElementById('apiList'),search=document.getElementById('search'),group=document.getElementById('group');
function show(key){const e=entries[key];if(!e){document.getElementById('articleBody').textContent='未找到该文档，请从左侧检索。';return;}
 document.getElementById('selectedTitle').textContent=e.name+' · '+e.title;
 document.getElementById('selectedMeta').textContent=e.group+' / '+(e.api?'Pro官方接口正文':'官方使用说明或导航记录');
 document.getElementById('articleBody').innerHTML=e.html;
 document.getElementById('copyLookup').dataset.command='python skills/finlab-strategy/scripts/api_docs.py '+(e.api?'--api '+e.name:'--doc-id '+e.name.slice(4));
 list.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.key===key));
 document.querySelectorAll('.copy').forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.parentNode.querySelector('code').textContent);b.textContent='已复制'}catch{b.textContent='请手动复制'}});
}
function render(){const q=search.value.trim().toLowerCase(),g=group.value;list.replaceChildren();let n=0;
 Object.values(entries).forEach(e=>{if(q&&!e.search.includes(q)||g&&e.group!==g)return;n++;
 const b=document.createElement('button');b.dataset.key=e.name;const name=document.createElement('strong');name.textContent=e.name;const sub=document.createElement('small');sub.textContent=e.title;b.append(name,sub);b.onclick=()=>{if(location.hash.slice(1)===e.name)show(e.name);else location.hash=e.name};list.appendChild(b);});
 document.getElementById('count').textContent='匹配 '+n+' / '+Object.keys(entries).length+' 份记录';
 list.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.key===location.hash.slice(1)));
}
search.addEventListener('input',render);group.addEventListener('change',render);window.addEventListener('hashchange',()=>show(location.hash.slice(1)));
document.getElementById('copyLookup').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('copyLookup').dataset.command);document.getElementById('copyLookup').textContent='已复制检索命令'}catch{document.getElementById('copyLookup').textContent=document.getElementById('copyLookup').dataset.command}};
render();show(location.hash.slice(1)||'daily');
"""
    page = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Tushare Pro · 离线 API 文档</title><style>' + style + '</style></head><body>'
            '<header><h1>Tushare Pro · 离线 API 文档</h1><p>'
            + str(snapshot['full_api_articles']) + ' 个Pro接口完整正文 · ' + str(snapshot['document_count'])
            + ' 份页面记录。参数、字段、单位、权限、限额和官方示例均可离线阅读；空白导航不计为完整API文档。</p>'
            '<nav><a href="system_v2.html#skills">返回系统技能说明</a><a href="../skills/finlab-strategy/SKILL.md">技能入口 Markdown</a></nav></header>'
            '<main><aside><label for="search">检索接口、字段或中文说明</label><input id="search" placeholder="daily / trade_date / 分钟 / 财务">'
            '<label for="group">资料分类</label><select id="group"><option value="">全部类别</option>' + options + '</select>'
            '<div id="count"></div><div id="apiList" aria-label="文档列表"></div></aside><article>'
            '<div class="notice">官方目录范围不等于任务权限。生成策略时使用 finlab SDK 与任务虚拟时间；不直接连接上游，不照搬官方样例日期。真实账号测试保持未实测。</div>'
            '<h2 id="selectedTitle"></h2><p class="meta" id="selectedMeta"></p><button id="copyLookup">复制单接口检索命令</button><div id="articleBody"></div>'
            '</article></main><script id="apiData" type="application/json">' + payload + '</script><script>' + script + '</script></body></html>')
    path = ROOT / 'docs/tushare_api_offline.html'
    textfile(path, page)
    return {"path": str(path), "full_api_articles": snapshot['full_api_articles'],
            "document_records": snapshot['document_count'], "external_assets": 0}
