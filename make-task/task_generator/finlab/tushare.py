"""Tushare-compatible entry point; all wrappers use the scoped bridge only."""
from __future__ import annotations
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .common import LabError

_token = None


class Rows(list):
    """Bridge records with pagination metadata; time values retain wire types."""
    def __init__(self, response):
        self.fields = response["data"]["fields"]
        self.meta = response.get("meta", {})
        super().__init__(dict(zip(self.fields, item)) for item in response["data"]["items"])

    def to_dataframe(self):
        import pandas as pd
        return pd.DataFrame(self, columns=self.fields)


def set_token(token):
    global _token
    _token = token


class ProClient:
    def __init__(self, url=None, token=None, *, dataframe=True, timeout=20):
        from urllib.parse import urlparse
        self.url = url or os.environ.get("MARKET_BRIDGE_URL", "http://127.0.0.1:8766")
        p = urlparse(self.url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise LabError("INVALID_BRIDGE_URL")
        self.token = token if token is not None else _token or os.environ.get("MARKET_BRIDGE_TOKEN", "")
        self.dataframe, self.timeout = dataframe, timeout

    def query(self, api_name, fields="", **params):
        body = json.dumps({"api_name": api_name, "token": self.token, "params": params, "fields": fields}, allow_nan=False).encode()
        req = Request(self.url, data=body, headers={"Content-Type": "application/json"})
        try:
            try:
                with urlopen(req, timeout=self.timeout) as response:
                    raw = response.read(50_000_001)
            except HTTPError as exc:
                raw = exc.read(65536)
                exc.close()
            reply = json.loads(raw)
        except (URLError, TimeoutError, OSError, ValueError):
            raise LabError("BRIDGE_UNAVAILABLE") from None
        if reply.get("code") != 0:
            raise LabError(reply.get("error_code", "QUERY_REJECTED"), reply.get("msg", "查询失败"),
                           request_id=reply.get("request_id"))
        rows = Rows(reply)
        return rows.to_dataframe() if self.dataframe else rows

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda fields="", **params: self.query(name, fields, **params)


def pro_api(token=None, *, url=None, dataframe=True):
    return ProClient(url, token, dataframe=dataframe)


def pro_bar(ts_code=None, api=None, start_date=None, end_date=None, freq="D", asset="E", adj=None,
            ma=None, factors=None, adjfactor=False, **kwargs):
    """Common daily/weekly/monthly/minute wrapper, including PIT adjustment.

    Unsupported wrapper options fail explicitly; no official SDK network path.
    """
    if kwargs or not ts_code or asset not in {"E", "FD", "I", "FT"}:
        raise LabError("WRAPPER_ARGUMENT_UNSUPPORTED")
    client = api or pro_api()
    params = {"ts_code": ts_code}
    if start_date: params["start_date"] = start_date
    if end_date: params["end_date"] = end_date
    freq = str(freq)
    names = {"E": "daily", "FD": "fund_daily", "I": "index_daily", "FT": "fut_daily"}
    if freq in {"1min", "5min", "15min", "30min", "60min"}:
        name = "ft_mins" if asset == "FT" else "etf_mins" if asset == "FD" else "idx_mins" if asset == "I" else "stk_mins"
        params["freq"] = freq
    elif freq in {"D", "W", "M"}:
        name = names[asset] if freq == "D" else {"E": {"W": "weekly", "M": "monthly"},
                  "I": {"W": "index_weekly", "M": "index_monthly"}}.get(asset, {}).get(freq)
        if name is None: raise LabError("WRAPPER_FREQUENCY_UNSUPPORTED")
    else:
        raise LabError("WRAPPER_FREQUENCY_UNSUPPORTED")
    rows, offset = [], 0
    list_client = ProClient(client.url, client.token, dataframe=False)
    import pandas as pd
    def all_rows(endpoint, query_params):
        values, offset, seen = [], 0, set()
        for _ in range(10000):
            batch = list_client.query(endpoint, **query_params, limit=2000, offset=offset)
            values.extend(batch)
            if not batch.meta.get("has_more"): return pd.DataFrame(values)
            next_offset = batch.meta.get("next_offset")
            if not isinstance(next_offset, int) or next_offset <= offset or next_offset in seen: raise LabError("PAGINATION_STALLED")
            seen.add(next_offset); offset = next_offset
        raise LabError("PAGINATION_LIMIT")
    frame = all_rows(name, params)
    if frame.empty: return frame
    date_col = "trade_date" if "trade_date" in frame else "trade_time"
    frame = frame.sort_values(date_col).reset_index(drop=True)
    if adj or adjfactor:
        if asset not in {"E", "FD"} or freq != "D" or adj not in {None, "qfq", "hfq"}:
            raise LabError("WRAPPER_ADJUSTMENT_UNSUPPORTED")
        factor_api = "adj_factor" if asset == "E" else "fund_adj"
        factors_df = all_rows(factor_api, {k: v for k, v in params.items() if k != "freq"})
        frame = frame.merge(factors_df[["trade_date", "adj_factor"]], on="trade_date", how="left", validate="one_to_one")
        if frame["adj_factor"].isna().any(): raise LabError("ADJUSTMENT_DATA_NOT_FROZEN")
        if adj:
            denominator = frame["adj_factor"].iloc[-1] if adj == "qfq" else 1.0
            for col in ("open", "high", "low", "close", "pre_close"):
                if col in frame: frame[col] = frame[col] * frame["adj_factor"] / denominator
        if not adjfactor: frame = frame.drop(columns=["adj_factor"])
    for window in ma or []:
        if not isinstance(window, int) or window < 1: raise LabError("INVALID_MA_WINDOW")
        frame["ma" + str(window)] = frame["close"].rolling(window).mean()
        frame["ma_v_" + str(window)] = frame["vol"].rolling(window).mean()
    if factors:
        if set(factors) - {"tor", "vr"}: raise LabError("WRAPPER_FACTOR_UNSUPPORTED")
        basic = all_rows("daily_basic", {k: v for k, v in params.items() if k != "freq"})
        cols = ["trade_date"] + (["turnover_rate"] if "tor" in factors else []) + (["volume_ratio"] if "vr" in factors else [])
        frame = frame.merge(basic[cols], on="trade_date", how="left", validate="one_to_one")
    return frame.sort_values(date_col, ascending=False).reset_index(drop=True)
