"""Resolve an explicitly declared futures product pool without widening it."""
import re
from .common import LabError

PRODUCT_EXCHANGES = {'B':'DCE','M':'DCE','Y':'DCE','FU':'SHF',
                     'SC':'INE','LU':'INE','FG':'ZCE','SA':'ZCE'}


def validate_symbol_selection(spec, symbols):
    allowed = set(spec.get('bridge_contract',{}).get('symbols',[]))
    futures = spec.get('environment_id') == 'akquant_futures_v2'
    resolved = []
    for symbol in symbols:
        if not isinstance(symbol,str) or not symbol:
            raise LabError('EXPLICIT_SYMBOLS_REQUIRED')
        if futures:
            match = re.fullmatch(r'([A-Za-z]+)(\d{3,4})\.([A-Z]+)',symbol)
            if not match or not 1 <= int(match[2][-2:]) <= 12:
                raise LabError('REAL_FUTURES_CONTRACT_REQUIRED', '期货下单及冻结需真实月份合约；品种池代号不能直接作为可交易代码')
            product, exchange = match[1].upper(), match[3]
            if PRODUCT_EXCHANGES.get(product) != exchange:
                raise LabError('FUTURES_EXCHANGE_MISMATCH', '合约品种或交易所超出当前登记的国内期货能力')
            if allowed and symbol not in allowed and product not in allowed:
                raise LabError('SYMBOL_OUTSIDE_APPROVED_SCOPE', '真实合约不属于规格明确允许的品种池或合约集合')
            resolved.append({'symbol':symbol,'product':product,'exchange':exchange})
        else:
            if allowed and symbol not in allowed:
                raise LabError('SYMBOL_OUTSIDE_APPROVED_SCOPE', '交易代码不在规格明确允许的标的集合')
            resolved.append({'symbol':symbol})
    return resolved
