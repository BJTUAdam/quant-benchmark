"""Embed the maintained strategy reference into the existing public allowlist.

The participant receives only public/, so every local reference must resolve
there. This module changes documentation, never grants data or runtime access.
"""
from pathlib import Path
import re
from urllib.parse import urlsplit

from .common import ROOT, LabError, textfile, read, write
from .catalog import endpoint


def local_links(text):
    mapping = {'strategy-sdk.md':'SUBMISSION.md', 'submission.md':'SUBMISSION.md',
               'tushare-bridge.md':'DATA_API.md', 'tushare-pitfalls.md':'DATA_API.md',
               'backtest-pitfalls.md':'SUBMISSION.md'}
    def replace(match):
        target = match[2]
        if urlsplit(target).scheme or target.startswith('#'):
            return match[0]
        filename = Path(target.split('#',1)[0]).name
        if filename in mapping:
            return '[' + match[1] + '](' + mapping[filename] + ')'
        # Keep the explanation without promising an absent file or script.
        return match[1]
    return re.sub(r'\[([^\]\n]+)\]\(([^)\s]+)\)', replace, text)


def _source(root, filename):
    for path in (Path(root)/'skills/finlab-strategy/references'/filename,
                 ROOT/'skills/finlab-strategy/references'/filename):
        if path.is_file(): return path.read_text(encoding='utf-8')
    raise LabError('AGENT_REFERENCE_MISSING', '生成题包需要完整工具包内的策略SDK说明')


def _cell(value):
    return str(value).replace('|','/').replace('\n',' ').replace('\r',' ')


def normalize_public_entry(public):
    """Normalize packaging metadata without rewriting reviewed research bytes.

    Existing acceptance records keep their original public snapshot and hash.
    Only the exported copy of task_input.draft.json is adapted for public/ use.
    """
    public = Path(public)
    entry = read(public/'task_input.draft.json')
    original = entry.get('initial_message',{}).get('content_file')
    if original not in {'TASK.md','public/TASK.md'}:
        raise LabError('PUBLIC_ENTRY_PATH_UNSUPPORTED')
    for field in ('binding_contract','runtime_profile'):
        target = entry.get(field)
        if not isinstance(target,str) or Path(target).name != target or not (public/target).is_file():
            raise LabError('PUBLIC_ENTRY_REFERENCE_MISSING')
    if not (public/'TASK.md').is_file(): raise LabError('PUBLIC_ENTRY_REFERENCE_MISSING')
    entry['initial_message']['content_file'] = 'TASK.md'
    entry['path_base'] = 'entry_directory'
    write(public/'task_input.draft.json',entry)
    return {'source_content_file':original,'exported_content_file':'TASK.md',
            'path_base':'entry_directory','reviewed_research_content_changed':False}


def write_references(root, public, contract):
    public = Path(public)
    sdk = local_links(_source(root,'strategy-sdk.md'))
    submission = _source(root,'submission.md')
    start = submission.index('## 模板与预检')
    stop = submission.index('## 授权研发运行',start)
    submission = submission[:start] + '''## 模板与预检

本题的五个模板已放在当前public目录。将strategy.py、backtest.py、params.json、RESEARCH.md和CHANGELOG.md复制到新的提交目录，修改策略与参数；其他说明文件不进入运行容器。

在已安装完整Finlab工具包的环境执行：

```text
finlab submission check SUBMISSION_DIR
```

检查只验证文件、语法、依赖与JSON格式，不能代替容器回调执行。模板保持空仓，只用于接口连通性；不能把它当作完成本题的研究结果。当前目录不提供make_submission.py或preflight.py脚本。

''' + submission[stop:]
    submission = local_links(submission)
    preface = '''# 本题提交与策略SDK离线说明

首轮读取同目录TASK.md，接口契约见DATA_API.md、bridge_contract.json与runtime_profile.json。所有task_input.draft.json中的路径均相对于该JSON所在目录；把public目录单独分发后仍应能找到TASK.md。

当前可以据题面设计并编写可配置代码；只有平台已提供冻结数据、任务桥接连接、事件时钟和运行规则后，才能实际查询和回测。策略容器预装finlab SDK，单独的题目ZIP不包含整个运行系统。不要直接导入官方tushare/akshare/akquant，不连接平台上游服务。

订单意图不等于成交：ctx.order和ctx.cancel返回None，账户不在同一回调里同步改变。on_trade没有order_id/client_tag，on_order的id和累计filled_quantity才可用于订单台账。可选择每个真实合约只保留一笔活动净订单，或定义确定的映射队列；不能读取不存在的事件字段完成影子仓位归属。

现货股票仅做多或减持实际可卖数量；期货buy/sell改变同一真实合约的有符号净仓，平台没有虚拟子账户或价差级原子订单。产品池中的FU/M等不能直接作为下单代码，需由平台先绑定真实合约，并在生成代码/参数时以已绑定的公开runtime_profile为准。

---

'''
    textfile(public/'SUBMISSION.md', preface + sdk + '\n\n---\n\n' + submission)
    names = {e['api_name'] for e in contract['endpoints']}
    market = 'fut_daily' if 'fut_daily' in names else 'daily' if 'daily' in names else 'fund_daily'
    intro = '''# 本题桥接数据契约与离线调用说明

只允许bridge_contract.json列出的接口和字段；下文描述SDK能力不自动扩展本题权限。没有平台注入的MARKET_BRIDGE_URL、任务桥接令牌和已冻结episode时，任何查询都还未就绪。策略不接触真实Tushare令牌或真实时间映射。

```python
from datetime import datetime

def read_current_history(ctx, symbol):
    # symbol由已绑定的公开运行配置确定，日期来自当前虚拟事件时钟。
    virtual_day = datetime.fromisoformat(ctx.now).strftime("%Y%m%d")
    return ctx.query("MARKET", fields="ts_code,trade_date,close",
                     ts_code=symbol, end_date=virtual_day, limit=2000)
```

示例只演示调用，不是题目策略；单次查询可能只有一页。期货需使用真实分合约代码，不能拿品种池代号或连续合约替代可交易合约。账户撮合只使用原始价格和平台规则，研究复权不能重复计入公司行为收益。

## 本题允许的字段

'''.replace('"MARKET"','"' + market + '"')
    tables = []
    for declared in contract['endpoints']:
        ep = endpoint(declared['api_name'])
        lines = [f'### {declared["api_name"]}', '', f'频率：{declared["frequency"]}。接口参数遵循版本化Tushare Pro目录，实际可用性与字段权限仍以本任务冻结契约为准。', '',
                 '|返回字段|桥接类型|含义与单位（目录说明）|','|---|---|---|']
        for field in declared['fields']:
            doc = ep.get('field_docs',{}).get(field,{})
            lines.append(f'|{field}|{_cell(ep["fields"][field])}|{_cell(doc.get("description","以绑定契约确认"))}|')
        lines += ['', '|查询参数|类型|目录说明|','|---|---|---|']
        for param, kind in ep['params'].items():
            doc = ep.get('param_docs',{}).get(param,{})
            lines.append(f'|{param}|{_cell(kind)}|{_cell(doc.get("description","桥接分页参数" if param in {"offset","limit"} else "须按当前接口规则确认"))}|')
        tables.append('\n'.join(lines))
    bridge = local_links(_source(root,'tushare-bridge.md'))
    bridge = bridge.replace('每个官方API的参数、输出单位、权限和限额见 离线索引。', '本题字段与参数列在上方；完整平台技能另含官方接口资料。')
    textfile(public/'DATA_API.md', intro + '\n\n'.join(tables) + '\n\n---\n\n' + bridge)
