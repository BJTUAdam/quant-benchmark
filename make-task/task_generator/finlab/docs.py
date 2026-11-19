"""Build one self-contained offline Chinese HTML from shipped protocols/reports."""
from __future__ import annotations
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from .common import ROOT, read, textfile, write
from .catalog import check_catalog, load_catalog

SECTIONS = [("integrated", "完整包与12题包", "TASK_PACKAGES.md"), ("overview", "系统结构", "SYSTEM.md"), ("setup", "首次本人配置", "SETUP.md"), ("tasks", "Task生产", "TASKS.md"),
            ("delivery", "交付与批跑", "DELIVERY.md"),
            ("generated", "DeepSeek新任务", "GENERATED_TASKS.md"),
            ("bridge", "Tushare桥接", "BRIDGE.md"), ("disclosure", "阶段范围与审计", "DISCLOSURE.md"),
            ("sdk", "策略与回测", "STRATEGY_SDK.md"),
            ("submission", "提交与结果", "SUBMISSION.md"), ("controller", "七段与恢复", "CONTROLLER.md"),
            ("operations", "部署与使用", "OPERATIONS.md"), ("ai", "AI阅读入口", "AI_README.md"),
            ("skills", "AI策略技能", "SKILLS.md")]


def inline(s):
    s = html.escape(s)
    def link(match):
        target = html.unescape(match[2])
        if urlsplit(target).scheme not in {"", "http", "https", "mailto"}:
            return match[1]
        return '<a href="' + html.escape(target, quote=True) + '">' + match[1] + '</a>'
    s = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


def render_file(path):
    """Resolve documentation links relative to their single maintained source."""
    document = markdown(path.read_text(encoding="utf-8"))
    def rewrite(match):
        target = html.unescape(match[1])
        if urlsplit(target).scheme or target.startswith(("#", "/")):
            return match[0]
        file, marker, anchor = target.partition("#")
        resolved = (path.parent / file).resolve()
        if not resolved.is_relative_to(ROOT):
            return match[0]
        relative = os.path.relpath(resolved, ROOT / "docs").replace("\\", "/")
        return 'href="' + html.escape(relative + (marker + anchor if marker else ""), quote=True) + '"'
    return re.sub(r'href="([^"]+)"', rewrite, document)


def markdown(s):
    out, code, table, listing = [], None, False, False
    def close():
        nonlocal table, listing
        if table: out.append("</tbody></table></div>"); table = False
        if listing: out.append("</ul>"); listing = False
    for line in s.splitlines():
        if line.startswith("```"):
            close()
            if code is None: code = []
            else:
                out.append('<div class="codebox"><button class="copy">复制</button><pre><code>' + html.escape("\n".join(code)) + '</code></pre></div>'); code = None
        elif code is not None: code.append(line)
        elif line.startswith("|"):
            if re.fullmatch(r"[|:\- ]+", line): continue
            cells = [cell.replace('\\|', '|') for cell in re.split(r'(?<!\\)\|', line.strip('|'))]
            if not table:
                if listing: out.append("</ul>"); listing = False
                table = True; out.append('<div class="tablewrap"><table><thead><tr>' + ''.join('<th>' + inline(c.strip()) + '</th>' for c in cells) + '</tr></thead><tbody>')
            else: out.append('<tr>' + ''.join('<td>' + inline(c.strip()) + '</td>' for c in cells) + '</tr>')
        elif line.startswith("#"):
            close(); n = min(4, len(line) - len(line.lstrip("#")))
            out.append(f'<h{n+1}>' + inline(line.lstrip("# ")) + f'</h{n+1}>')
        elif line.startswith("- "):
            if table: close()
            if not listing: out.append('<ul>'); listing = True
            out.append('<li>' + inline(line[2:]) + '</li>')
        elif line.strip():
            close(); out.append('<p>' + inline(line) + '</p>')
        else: close()
    close()
    return "\n".join(out)


STYLE = '''
:root{--ink:#182b33;--muted:#52646b;--accent:#146b55;--gold:#b75b22;--line:#dce5df;--bg:#f5f7f3}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif}
aside{position:fixed;inset:0 auto 0 0;width:245px;padding:32px 23px;background:#143c35;color:#f0f6f1;overflow:auto}
.brand{font-size:25px;font-weight:760;line-height:1.3;letter-spacing:-.5px}.brand small{display:block;font-size:12px;letter-spacing:2px;font-weight:500;color:#accbbd;margin-top:15px}
nav{margin-top:28px}nav a{display:block;color:#cee1d5;text-decoration:none;padding:8px 12px;border-radius:6px;margin:3px 0;font-size:14px}nav a:hover,nav a.active{color:white;background:#285e4e}
aside .note{font-size:12px;color:#acbfb6;margin-top:28px}main{margin-left:245px;max-width:1420px;padding:40px 6vw 90px}.eyebrow{color:var(--accent);font-size:12px;letter-spacing:2px;font-weight:700}
h1{font-size:clamp(30px,3.5vw,52px);line-height:1.22;max-width:850px;letter-spacing:-1px;margin:16px 0 22px}h2{font-size:28px;line-height:1.45;margin:0 0 20px}h3{font-size:22px;margin:28px 0 12px}h4{font-size:17px;margin:25px 0 10px}p{margin:12px 0;overflow-wrap:anywhere}.lede{font-size:19px;color:var(--muted);max-width:840px}.chips{display:flex;flex-wrap:wrap;gap:9px;margin:22px 0}.chip{background:#e0eee6;border:1px solid #c3dccc;color:#145f4c;padding:4px 12px;border-radius:30px;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.card{padding:22px;border:1px solid var(--line);background:white;border-radius:12px;min-width:0}.card strong{font-size:18px;display:block}.card p{font-size:14px}.num{font-size:32px;font-weight:750;color:var(--accent)}.section{background:#fff;padding:30px 34px;border:1px solid var(--line);border-radius:14px;margin-top:25px;scroll-margin-top:18px}.diagram{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin:24px 0}.node{background:#eef4ee;border:1px solid #c8ddd1;border-radius:8px;padding:16px;flex:1;min-width:125px;text-align:center;font-size:14px}.arrow{align-self:center;color:var(--accent)}
.notice{border-left:4px solid #bc652d;background:#fff5e9;padding:15px 20px;margin:20px 0;font-size:14px}.codebox{position:relative;margin:16px 0}pre{background:#152e30;color:#e3f1e9;border-radius:9px;padding:25px 22px;overflow:auto;font-size:13px;line-height:1.8;tab-size:2}code{font-family:Consolas,monospace;font-size:.9em}p code,li code{background:#edf2ed;padding:2px 4px;border-radius:4px;overflow-wrap:anywhere}.copy{position:absolute;right:10px;top:9px;color:#d1e6d5;border:1px solid #4b695e;background:#26433d;border-radius:5px;padding:4px 9px;cursor:pointer}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:11px 13px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf4ee;font-weight:650}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:8px;margin:16px 0}.scroll{max-height:380px;overflow:auto}details{border:1px solid var(--line);padding:12px 16px;border-radius:7px;margin:9px 0}summary{cursor:pointer;font-weight:600}.tree details{margin-left:12px}.tree span{font-size:14px;color:var(--muted)}.controls{display:flex;gap:20px;flex-wrap:wrap;align-items:center}input,select{font:inherit;padding:8px;border:1px solid #b8c9bc;border-radius:5px;max-width:100%}input[type=range]{width:230px;padding:0}.result{font-size:24px;color:var(--accent);padding:18px 0}.muted{color:var(--muted);font-size:14px}.phase{display:flex;gap:5px;margin-top:16px}.phase div{flex:1;text-align:center;background:#f0f1ee;color:#6c7971;padding:12px 0;border-radius:5px;font-size:13px}.phase .open{background:#d4e8dc;color:#195f47}.status-ok{color:#17654d}.status-pending{color:#ac5624}.search{width:100%;margin:12px 0}.foot{color:#617168;font-size:13px;margin-top:25px}a{color:#146b55}
#catalog table{min-width:1050px;table-layout:fixed}#catalog th:first-child{width:45%}#catalog td pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:1000px){aside{width:200px;padding:25px 14px}main{margin-left:200px;padding:30px}.cards{grid-template-columns:1fr 1fr}.section{padding:24px}}
@media(max-width:700px){aside{position:static;width:auto;padding:20px}.brand small{margin:8px 0}nav{display:flex;flex-wrap:wrap;gap:3px;margin-top:16px}nav a{font-size:12px;padding:5px 8px}aside .note{display:none}main{margin:0;padding:22px 15px 60px}.cards{grid-template-columns:1fr}.section{padding:20px 16px}h1{font-size:33px}h2{font-size:24px}.node{flex-basis:42%}.arrow{display:none}.phase{flex-wrap:wrap}.phase div{min-width:20%}}
@media print{aside,.copy,.controls{display:none}main{margin:0;padding:0}.section{break-inside:avoid;border:0}.scroll{max-height:none}}
'''


SCRIPT = '''
document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',async()=>{const s=b.parentNode.querySelector('code').textContent;try{await navigator.clipboard.writeText(s);b.textContent='已复制'}catch{const t=document.createElement('textarea');t.value=s;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();b.textContent='已复制'}setTimeout(()=>b.textContent='复制',1200)}));
function mapDate(){const s=document.getElementById('realdate').value, y=Number(document.getElementById('offset').value);let o='请输入有效日期';if(s){const [yy,m,d]=s.split('-').map(Number), n=new Date(Date.UTC(yy+y,m-1,d));o=n.getUTCMonth()===m-1&&n.getUTCDate()===d?`${yy+y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`:'映射无效：闰日不可裁剪'}document.getElementById('virtualdate').textContent=o}
document.getElementById('realdate').addEventListener('input',mapDate);document.getElementById('offset').addEventListener('change',mapDate);mapDate();
function phase(){let n=+document.getElementById('release').value;document.getElementById('releaseLabel').textContent=`已成功提交 ${n} / 7 段`;document.querySelectorAll('.phase div').forEach((e,i)=>e.classList.toggle('open',i<=n));document.getElementById('releaseExplain').textContent=n===0?'研发端仅见研发期；策略端按当前事件时钟查询。':`研发端可见研发期与正式第1—${n}段。后续段仍受限；失败运行不会推进此状态。`}
document.getElementById('release').addEventListener('input',phase);phase();
function queryRangeDemo(){const a=document.getElementById('queryStart').value,b=document.getElementById('queryEnd').value,c=document.getElementById('queryClock').checked;let result;if(!a||!b||a>b){result={http:400,error_code:'INVALID_TIME_SELECTION',msg:'请输入有效且有序的查询日期。'}}else if(a<'2400-01-01'||b>='2400-02-01'||c&&b>'2400-01-20'){result={http:403,code:-1,error_code:'TIME_RANGE_EXCEEDED',msg:'查询时间超出本阶段允许披露范围，本次请求未返回数据。',request_id:'DEMO_ONLY_NOT_AN_ACTUAL_REQUEST'}}else{result={http:200,code:0,msg:'范围检查通过（演示，未发起查询；数据存在性另行核验）。'}}document.getElementById('queryOutcome').textContent=JSON.stringify(result,null,2)}
['queryStart','queryEnd','queryClock'].forEach(id=>document.getElementById(id).addEventListener('input',queryRangeDemo));queryRangeDemo();
document.getElementById('apiSearch').addEventListener('input',e=>{let q=e.target.value.toLowerCase();document.querySelectorAll('#apiRows tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))});
const observer=new IntersectionObserver(rows=>{rows.forEach(e=>{if(e.isIntersecting){document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.hash==='#'+e.target.id))}})},{rootMargin:'-10% 0px -60% 0px'});document.querySelectorAll('main section[id]').forEach(e=>observer.observe(e));
'''


def build():
    skill_snapshot = ROOT / "skills/finlab-strategy/references/tushare/snapshot.json"
    skill_browser = None
    if skill_snapshot.is_file():
        from .skill_docs import build_api_browser
        skill_browser = build_api_browser()
    coverage = check_catalog()
    write(ROOT / "docs/catalog_coverage_v2.json", coverage)
    acceptance = read(ROOT / "docs/acceptance_v2.json") if (ROOT / "docs/acceptance_v2.json").is_file() else {"status": "not_run"}
    units = read(ROOT / "docs/unit_tests_v2.json") if (ROOT / "docs/unit_tests_v2.json").is_file() else {"status": "not_run", "tests": 0}
    security = read(ROOT / "docs/security_dns_v2.json") if (ROOT / "docs/security_dns_v2.json").is_file() else {"status":"not_run"}
    manifest = read(ROOT / "examples/v2/manifest.json")
    nav = ''.join(f'<a href="#{key}">{title}</a>' for key, title, _ in SECTIONS) + '<a href="#automatic">自动入库重试</a><a href="#interactive">时间与分段演示</a><a href="#examples">完整任务包</a><a href="#catalog">接口覆盖</a><a href="#verification">实际验收</a>'
    body = ['<header><div class="eyebrow">FINLAB / V2 SYSTEM HANDBOOK</div><h1>持续生产研究任务，<br>在受控环境中长期运行。</h1><p class="lede">Task 生产 · Tushare 时间桥接 · AKQuant Docker 回测。数据通过接口提供，参与者提交可执行代码，平台保存连续账户和验证记录。</p><div class="chips"><span class="chip">离线文档</span><span class="chip">Python 3.12</span><span class="chip">AKQuant 0.3.76</span><span class="chip">真实名称与代码</span><span class="chip">未来虚拟日期</span><span class="chip">本人选择运行预算</span><span class="chip">七段连续运行</span></div></header>']
    body.append(f'<div class="cards"><div class="card"><div class="num">6</div><strong>方向与规格草案</strong><p>原生v2结构；历史对照继续去重。当前示例均未人工批准。</p></div><div class="card"><div class="num">{coverage["api_count"]}</div><strong>官方 Pro 接口定义</strong><p>协议与字段目录已收录；账号权限、回放证据和真实实测分别记录。</p></div><div class="card"><div class="num">{units.get("tests",0)}</div><strong>自动测试</strong><p>本页从实际检查报告构建，Docker验收状态：{html.escape(acceptance["status"])}。</p></div></div>')
    body.append('<div class="diagram">' + '<span class="arrow">→</span>'.join(f'<div class="node">{x}</div>' for x in ['候选生成与去重','程序＋模型自动接纳','接口与规则绑定','冻结数据版本','研发＋七段回测','结果与检查点归档']) + '</div>')
    dirs = {"taskgen/":"模型API、去重、审核与导出", "finlab/":"桥接、策略SDK、Docker控制器", "catalog/":"来源、历史题、Pro目录与规则", "schemas/":"任务与协议格式", "config/":"模型和运营模板", "examples/v2/":"六类未批准规格示例", "examples/generated/":"DeepSeek真实生成的未批准候选与去敏证据；只读去重对照", "task_packages/20261110/":"十二个修订规格；展开目录与单独ZIP；源码修复已合并", "skills/finlab-strategy/":"策略AI技能、离线接口资料、预检与空仓模板", "registry/":"身份、版本与批准记录", "deployment/":"镜像、依赖和镜像ID锁", "docs/v2/":"供开发AI阅读的Markdown", "work_v2/":"运营私有数据、检查点、运行日志", "dist/":"完整与分工具包"}
    tree = '<details open class="tree"><summary>task_generator/</summary>' + ''.join('<details><summary>' + k + '</summary><span>' + v + '</span></details>' for k,v in dirs.items()) + '</details>'
    for key, title, filename in SECTIONS:
        source = ROOT / "docs/v2" / filename
        canonical = {"sdk": "strategy-sdk.md", "submission": "submission.md"}.get(key)
        shared = ROOT / "skills/finlab-strategy/references" / canonical if canonical else None
        content = render_file(shared if shared and shared.is_file() else source)
        if key == 'integrated':
            from .task_packages import TASK_PACKAGE_PATH, RELEASE, verify as verify_task_packages
            package_check = verify_task_packages()
            package_index = read(ROOT/TASK_PACKAGE_PATH/'任务清单.json')
            content += '<div class="notice"><strong>本包已合并修复，无需另打补丁。</strong><p>发行版本：' + html.escape(RELEASE) + '。下表是实际交付的十二题，每类两份；展开目录和单独ZIP都在完整包内。</p></div>'
            content += '<div class="tablewrap"><table><thead><tr><th>序号</th><th>方向</th><th>题目</th><th>下载/阅读</th></tr></thead><tbody>'
            for item in package_index['tasks']:
                base='../'+TASK_PACKAGE_PATH+'/'
                content += '<tr><td>' + str(item['number']) + '</td><td>' + html.escape(item['family']) + '</td><td>' + html.escape(item['title']) + '</td><td><a href="' + base + item['archive'] + '">单独ZIP</a> · <a href="' + base + item['directory'] + '/public/TASK.md">TASK.md</a></td></tr>'
            content += '</tbody></table></div><p>状态：程序/会话审阅完成，外部API复核、自动接纳、数据绑定与正式回测待完成。</p>'
            for item in package_index['tasks']:
                task=ROOT/TASK_PACKAGE_PATH/item['directory']/'public/TASK.md'
                content += '<details><summary>' + str(item['number']).zfill(2) + ' · ' + html.escape(item['title']) + '</summary>' + markdown(task.read_text(encoding='utf-8')) + '</details>'
        if key == 'tasks' and (ROOT / 'examples/accepted/manifest.json').is_file():
            library = read(ROOT / 'examples/accepted/manifest.json')
            content += '<h3>本包自动接纳版本库</h3><p>以下规格实际通过自动入库；人工批准为0。重复和变体可继续绑定运行，独立新题计数保持0。数据与运行仍需完成各自检查。</p><div class="tablewrap"><table><thead><tr><th>题目</th><th>分类</th><th>版本</th><th>来源</th></tr></thead><tbody>'
            for filename in library['files']:
                item = read(ROOT / 'examples/accepted' / filename)
                content += '<tr><td>' + html.escape(item['spec']['title']) + '</td><td>' + html.escape(item['novelty']) + '</td><td>1</td><td>程序＋真实模型证据</td></tr>'
            content += '</tbody></table></div><p>新解压后执行 <code>finlab task init</code> 核验并装载版本库，使用 <code>finlab task approved</code> 查看自动来源。</p>'
        if key == "skills" and skill_browser:
            content += '<p><a href="tushare_api_offline.html">打开可搜索的离线 API 全文手册</a> · '
            content += str(skill_browser['full_api_articles']) + ' 个完整接口正文，' + str(skill_browser['document_records']) + ' 份页面记录。</p>'
        if key == "generated":
            from taskgen.batch_snapshot import verify_snapshot
            for folder in sorted((ROOT / "examples/generated").glob("*")):
                if folder.name.startswith(".candidate-snapshot-"):
                    continue
                verify_snapshot(folder, ROOT)
                snapshot = read(folder / "manifest.json")
                content += '<h3>展开本轮候选首轮正文</h3><p class="muted">以下是实际交付文件。模型判定为重复、变体或无法确定的题，均保持待审。</p>'
                for row in snapshot["retained_candidates"]:
                    task = folder / row["path"] / "public/TASK.md"
                    label = row["family_id"] + " · " + row["title"]
                    status = row["classification"] + " / " + str(row["model_review_status"])
                    content += '<details><summary>' + html.escape(label) + '</summary><p class="status-pending">' + html.escape(status) + ' · 未人工批准</p>' + markdown(task.read_text(encoding="utf-8")) + '</details>'
        if key == "disclosure":
            content += '''<div class="notice"><strong>交互示例 · 不发起数据请求，不修改平台范围</strong><p>构造的阶段范围为 [2400-01-01, 2400-02-01)。正式策略事件时钟示例为2400-01-20 15:00。日期均为虚拟标签。</p><div class="controls"><label>查询开始日 <input id="queryStart" type="date" value="2400-01-10"></label><label>查询结束日（含当天） <input id="queryEnd" type="date" value="2400-02-01"></label><label><input id="queryClock" type="checkbox" checked> 同时应用正式策略事件时钟</label></div><pre><code id="queryOutcome"></code></pre><p class="muted">真实请求的错误编号由服务生成并写入私有SQLite；本示例不会产生真实日志或行情。</p></div>'''
        body.append(f'<section id="{key}" class="section">' + (tree if key == "overview" else '') + content + '</section>')
    body.append('''<section id="interactive" class="section"><h2>时间映射与七段可见性</h2><p>本节仅供运营方理解映射，不交给策略AI。平台默认向未来偏移376年；公开任务只说明这是虚拟时间，不公开偏移量。虚拟年份不对应现实历史事件，不能据此预判股灾、政策或节假日。</p><div class="controls"><label>真实日期 <input id="realdate" type="date" value="2024-02-29"></label><label>年份偏移 <select id="offset"><option value="376">＋376年（平台默认）</option><option value="388">＋388年（示例）</option><option value="100">＋100年（测试世纪闰年）</option></select></label></div><div class="result">虚拟日期：<strong id="virtualdate"></strong></div><div class="notice">日期映射可逆，星期可能变化；股票、ETF、指数和合约名称代码保持原样，包括其中嵌入的年月。该例外及行情特征仍可能提供时间线索。</div><hr><div class="controls"><label id="releaseLabel"></label><input id="release" type="range" min="0" max="7" value="0"></div><div class="phase"><div>研发期</div><div>第1段</div><div>第2段</div><div>第3段</div><div>第4段</div><div>第5段</div><div>第6段</div><div>第7段</div></div><p id="releaseExplain"></p><p class="muted">滑块仅解释协议，不操作登记库、不释放数据。</p></section>''')
    examples = []
    for row in manifest["candidates"]:
        base = ROOT / row["path"] / "public"
        files = ''.join('<details><summary>' + html.escape(p.name) + '</summary><pre><code>' + html.escape(p.read_text(encoding="utf-8")) + '</code></pre></details>' for p in sorted(base.iterdir()) if p.is_file())
        examples.append('<details><summary>' + html.escape(row["family_id"] + ' · ' + row["title"]) + '</summary><p class="status-pending">旧种子示例 · 尚未接纳 · 数据待绑定 · 运行待接入；新工作流不要求人工审批</p>' + files + '</details>')
    body.append('<section id="examples" class="section"><h2>六类完整候选包</h2><p>每个折叠项包含实际交付的完整public文件；模板保持空仓，不提供参考答案。</p>' + ''.join(examples) + '</section>')
    catalog = load_catalog()
    rows = ''
    for r in coverage['endpoints']:
        ep = catalog['endpoints'][r['api_name']]
        detail = json.dumps({"params":ep['params'],"fields":ep['fields'],"pagination":r['pagination'],"permission":r['permission']},ensure_ascii=False,indent=2)
        rows += ('<tr><td><details><summary><code>' + html.escape(r['api_name']) + '</code></summary><pre><code>'+html.escape(detail)+'</code></pre></details></td><td>'
                 + str(r['parameter_count'])+' / '+str(r['field_count'])+'</td><td>'+('需补规则' if r['unclassified'] else '逐值校验')
                 +'</td><td>'+('类型往返通过' if r['mock_schema_roundtrip']=='passed_synthetic_types_only' else '需补规则')
                 +'</td><td>待任务证明</td><td>未实测</td></tr>')
    body.append(f'<section id="catalog" class="section"><h2>Pro接口目录 · {coverage["api_count"]} 项</h2><p>本表为目录与类型覆盖，不代表账号权限或实盘验证。展开接口查看参数、字段类型和分页约定。模拟检查仅使用构造值执行类型往返；实时/修订数据的历史可见性须逐题确认。</p><input class="search" id="apiSearch" placeholder="搜索 daily / fund / fut / income…" aria-label="搜索接口"><div class="tablewrap scroll"><table><thead><tr><th>接口与规则</th><th>参数/字段数</th><th>时间规则</th><th>模拟检查</th><th>历史回放</th><th>真实上游</th></tr></thead><tbody id="apiRows">{rows}</tbody></table></div></section>')
    body.append('<section id="verification" class="section"><h2>自动测试与Docker合成验收</h2><p>自动测试：' + html.escape(str(units.get("status"))) + '，共 ' + str(units.get("tests",0)) + ' 项。</p><pre><code>' + html.escape(json.dumps({k:v for k,v in acceptance.items() if k != "private_evidence"}, ensure_ascii=False, indent=2)) + '</code></pre><p>Docker报告使用明确标记的合成数据。自建Tushare服务的逐接口样本测试单独记录；样本有返回不代表范围完整、历史可见性或全部接口已验证。</p></section>')
    for report_name, title in [('integrated_release_v2.json', '本次整合发行检查'), ('task12_usability_v2.json', '十二题可用性检查'), ('upstream_probe_v2.json', '自建服务实际样本探测'), ('delivery_readiness_v2.json', '本机真实任务交付状态'), ('delivery_validation_v2.json', '交付链路历史验证'), ('automatic_acceptance_retry_v2.json', '自动入库真实重试'), ('automatic_upstream_retry_v2.json', '数据接口重试'), ('automatic_retry_data_v2.json', '采集与冻结检查')]:
        report_path = ROOT / 'docs' / report_name
        if report_path.is_file():
            body.append('<section ' + ('id="automatic" ' if report_name == 'automatic_acceptance_retry_v2.json' else '') + 'class="section"><h2>' + title + '</h2><pre><code>' + html.escape(json.dumps(read(report_path), ensure_ascii=False, indent=2)) + '</code></pre></section>')
    body.append('<section class="section"><h2>DNS与容器隔离实测</h2><p>以下摘要来自当前固定镜像的实际合成Docker隔离场景；对应完整结果保存在acceptance_v2.json。</p><pre><code>' + html.escape(json.dumps({k:v for k,v in security.items() if k!='public_results'},ensure_ascii=False,indent=2)) + '</code></pre></section>')
    body.append('<p class="foot">金融长程系统 v2 · 本页自包含，无外部字体、脚本或数据请求。规格自动接纳；正式段由控制器提交。AI入口：docs/v2/AI_README.md。</p>')
    page = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>金融长程系统 v2 · 使用与原理</title><style>' + STYLE + '</style></head><body><aside><div class="brand">金融长程<br>任务系统<small>FINLAB V2 · HANDBOOK</small></div><nav>' + nav + '</nav><p class="note">用于平台开发与任务生产。<br>参赛agent仅接收public任务包。</p></aside><main>' + ''.join(body) + '</main><script>' + SCRIPT + '</script></body></html>'
    textfile(ROOT / "docs/system_v2.html", page)
    old = ROOT / "docs/index.html"
    textfile(old, page)
    return {"html": str(old), "standalone_html": str(ROOT / "docs/system_v2.html"), "api_count": coverage["api_count"],
            "offline_api_browser": skill_browser}
