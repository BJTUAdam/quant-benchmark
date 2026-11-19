"""Append-only, credential-free API accounting, outside the agent workspace.

Prices are conservative list-price estimates, not a provider invoice. A request
reserves the configured model input ceiling and complete output ceiling before
network access. Missing usage and interrupted requests retain their reservation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import uuid
from urllib.parse import urlparse


class BudgetError(RuntimeError):
    pass


def validate_policy(policy: dict) -> None:
    if not isinstance(policy, dict) or set(policy) != {
        'budget_cny', 'input_cny_per_million', 'output_cny_per_million',
        'input_token_ceiling', 'price_reference'}:
        raise BudgetError('费用规则字段不完整或含未知字段')
    try:
        for key in ('budget_cny', 'input_cny_per_million', 'output_cny_per_million'):
            value = Decimal(str(policy[key]))
            if isinstance(policy[key], bool) or not value.is_finite() or value <= 0:
                raise ValueError(key)
        ceiling = policy['input_token_ceiling']
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or not 1024 <= ceiling <= 2000000:
            raise ValueError('input_token_ceiling')
        source = urlparse(policy['price_reference'])
        if source.scheme != 'https' or not source.netloc or source.username or source.password:
            raise ValueError('price_reference')
    except (ValueError, InvalidOperation, TypeError):
        raise BudgetError('费用规则数值或价格来源不合法') from None


def safe_usage(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in ('prompt_tokens', 'completion_tokens'):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        result[key] = value
    for group, key in (('prompt_tokens_details', 'cached_tokens'), ('completion_tokens_details', 'reasoning_tokens')):
        details = raw.get(group)
        value = details.get(key) if isinstance(details, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    if result.get('cached_tokens', 0) > result['prompt_tokens'] or result.get('reasoning_tokens', 0) > result['completion_tokens']:
        return None
    # completion_tokens already includes reasoning; never add reasoning twice.
    return result


def totals(records: list[dict]) -> dict:
    requests = {}
    prompt = completion = reasoning = cached = 0
    for r in records:
        if r['event'] == 'reserved':
            if r['request_id'] in requests:
                raise BudgetError('费用记录出现重复请求')
            requests[r['request_id']] = {'reserved': Decimal(r['reserved_cny']), 'settled': None}
        elif r['event'] == 'settled':
            item = requests.get(r['request_id'])
            if item is None or item['settled'] is not None:
                raise BudgetError('费用记录结算顺序异常')
            item['settled'] = Decimal(r['estimated_cny'])
            prompt += r['usage']['prompt_tokens']
            completion += r['usage']['completion_tokens']
            reasoning += r['usage'].get('reasoning_tokens', 0)
            cached += r['usage'].get('cached_tokens', 0)
    spent = sum((v['settled'] for v in requests.values() if v['settled'] is not None), Decimal(0))
    held = sum((v['reserved'] for v in requests.values() if v['settled'] is None), Decimal(0))
    return {'requests': len(requests), 'settled_requests': sum(v['settled'] is not None for v in requests.values()),
            'estimated_cny': str(spent), 'unconfirmed_reservations_cny': str(held),
            'budget_accounted_cny': str(spent+held), 'prompt_tokens': prompt,
            'completion_tokens': completion, 'reasoning_tokens': reasoning, 'cached_tokens': cached}


class UsageLedger:
    def __init__(self, path: str | Path, cfg: dict):
        validate_policy(cfg['cost_control'])
        if cfg['provider'] != 'openai_compatible' or cfg.get('output_token_parameter') != 'max_completion_tokens':
            raise BudgetError('费用控制要求 OpenAI 兼容协议及包含思考的 max_completion_tokens')
        self.path = Path(path)
        self.cfg = cfg
        self.policy = cfg['cost_control']
        owner = cfg.get('_ledger_owner', cfg)
        self.budget_policy = owner['cost_control']
        # User-side calls share the registered budget/header, with a smaller
        # per-request output reservation. The tested model's ceiling stays fixed.
        ledger_max_tokens = cfg.get('_ledger_max_tokens', owner['max_tokens'])
        if type(ledger_max_tokens) is not int or ledger_max_tokens < cfg['max_tokens']:
            raise BudgetError('费用台账输出上限不能低于本次请求上限')
        self.header = {'event':'policy', 'schema_version':1,
                       'model':owner['model'], 'base_url':owner['base_url'],
                       'max_completion_tokens':ledger_max_tokens, 'cost_control':self.budget_policy}
        independent = (owner.get('user_agent') or {}).get('model_config')
        if independent:
            # Lock both price schedules in one ledger; both roles draw from one budget.
            self.header['user_agent_model'] = independent

    @contextmanager
    def locked(self):
        import fcntl  # Production harness explicitly requires Linux / WSL.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix+'.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    try:
                        records = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
                    except (ValueError, UnicodeDecodeError):
                        raise BudgetError('费用台账不完整或损坏，停止调用') from None
                    if not records or records[0] != self.header:
                        raise BudgetError('费用台账规则已固定，禁止中途更换模型、价格或预算')
                else:
                    self.append(self.header)
                    records = [self.header]
                yield records
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def append(self, record):
        with self.path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False, separators=(',',':'))+'\n')
            output.flush()
            os.fsync(output.fileno())

    def reserve(self, turn: str, attempt: int) -> str:
        # Official docs allow a +/-10-token discrepancy at the output limit.
        bound = (Decimal(self.policy['input_token_ceiling'])*Decimal(str(self.policy['input_cny_per_million']))
                 + Decimal(self.cfg['max_tokens']+10)*Decimal(str(self.policy['output_cny_per_million'])))/1000000
        with self.locked() as records:
            accounted = Decimal(totals(records)['budget_accounted_cny'])
            if accounted+bound > Decimal(str(self.budget_policy['budget_cny'])):
                raise BudgetError(f'API 费用预算不足：已记账或预留 {accounted:.4f} 元，下一请求最多预留 {bound:.4f} 元，上限 {self.budget_policy["budget_cny"]} 元；未发送请求，本阶段不提交')
            request_id = uuid.uuid4().hex
            self.append({'event':'reserved','request_id':request_id,'turn':turn,'attempt':attempt,
                         'model': self.cfg['model'], 'base_url': self.cfg['base_url'],
                         'reserved_cny':str(bound),'utc':datetime.now(timezone.utc).isoformat()})
            return request_id

    def settle(self, request_id: str, raw_usage: dict | None) -> None:
        usage = safe_usage(raw_usage)
        if usage is None:
            raise BudgetError('API 未返回完整可计费 usage；保留请求预留金额并停止，不猜测费用')
        amount = (Decimal(usage['prompt_tokens'])*Decimal(str(self.policy['input_cny_per_million']))
                  + Decimal(usage['completion_tokens'])*Decimal(str(self.policy['output_cny_per_million'])))/1000000
        with self.locked() as records:
            reservations = [r for r in records if r.get('request_id')==request_id and r['event']=='reserved']
            if len(reservations)!=1 or any(r.get('request_id')==request_id and r['event']=='settled' for r in records):
                raise BudgetError('费用请求缺失或已经结算')
            self.append({'event':'settled','request_id':request_id,'usage':usage,
                         'estimated_cny':str(amount),'utc':datetime.now(timezone.utc).isoformat()})
            if usage['prompt_tokens']>self.policy['input_token_ceiling'] or usage['completion_tokens']>self.cfg['max_tokens']+10:
                raise BudgetError('供应商 usage 超过已声明 Token 上限；已记录实际用量并停止')

    def failed(self, request_id: str, category: str) -> None:
        # A disconnected request may already have been billed. Keep reservation.
        with self.locked():
            self.append({'event':'unconfirmed','request_id':request_id,'category':category,
                         'utc':datetime.now(timezone.utc).isoformat()})
