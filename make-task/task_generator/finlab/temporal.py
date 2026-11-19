"""Typed, reversible year mapping. Instrument identities are explicit exceptions."""
from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
import re
import math
from .common import LabError

SH = timezone(timedelta(hours=8))
# A non-round future offset keeps contemporary observations away from a real
# historical-year interpretation. The private freeze manifest pins its value.
DEFAULT_YEAR_OFFSET = 376
TEXT_YEAR = r"(?:1[89]|[2-9]\d)\d{2}"
DATE_TOKEN = re.compile(r"(?<!\d)(?:\d{14}|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8}|\d{4}年\d{1,2}月\d{1,2}日|\d{4}[Qq][1-4]|\d{4}[-/]\d{2}|" + TEXT_YEAR + r"\d{2}|" + TEXT_YEAR + r")(?!\d)")


def parse_time(value):
    try:
        s = str(value)
        if re.fullmatch(r"\d{8}", s):
            d = datetime.strptime(s, "%Y%m%d").replace(tzinfo=SH)
        else:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=SH)
        return d.astimezone(SH)
    except (ValueError, TypeError, OverflowError):
        raise LabError("INVALID_TIME") from None


def iso(value):
    result = parse_time(value).isoformat(timespec="microseconds")
    # Keep registered nanoseconds on the private timeline as well. Dropping
    # these digits could make a later observation appear available early.
    match = re.search(r"[T ]\d\d:\d\d:\d\d\.(\d+)", str(value))
    if match and len(match[1]) > 6:
        if len(match[1]) > 9 and any(c != "0" for c in match[1][9:]):
            raise LabError("UNCLASSIFIED_TIME_FORMAT")
        tail = (match[1] + "000000000")[6:9]
        if tail != "000":
            result = result[:-6] + tail + result[-6:]
    return result


class TimeMap:
    def __init__(self, years=DEFAULT_YEAR_OFFSET):
        if isinstance(years, bool) or not isinstance(years, int) or years == 0 or abs(years) > 400:
            raise LabError("INVALID_YEAR_OFFSET")
        self.years = years

    def shift(self, value, kind="date", *, inverse=False):
        if value is None or value == "":
            return value
        delta = -self.years if inverse else self.years
        try:
            if isinstance(value, (dict, list, tuple)):
                raise LabError("NESTED_TIME_RULE_REQUIRED")
            if kind in {"identity", "number", "boolean", "duration", "time_of_day", "enum"}:
                if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise LabError("FIELD_TYPE_MISMATCH")
                return value
            if kind == "year":
                year = int(value) + delta
                if not 1 <= year <= 9999:
                    raise ValueError()
                return year if isinstance(value, int) else f"{year:04d}"
            if kind in {"month", "quarter", "period"}:
                s = str(value)
                if kind == "month" and re.fullmatch(r"\d{1,2}", s) and 1 <= int(s) <= 12:
                    return value  # month-of-year contains no year to hide
                if not re.fullmatch(r"\d{4}(?:[-/]?\d{2}|[Qq][1-4]|年(?:第)?[1-4]季度)", s):
                    raise ValueError()
                if kind == "month" and not 1 <= int(s[-2:]) <= 12:
                    raise ValueError()
                year = int(s[:4]) + delta
                if not 1 <= year <= 9999:
                    raise ValueError()
                return f"{year:04d}" + s[4:]
            if kind.startswith("epoch_"):
                factor = {"epoch_s": 1, "epoch_ms": 1000, "epoch_us": 1000000, "epoch_ns": 1000000000}[kind]
                v = int(value)
                if isinstance(value, float) and value != v:
                    raise ValueError()
                sec, rem = divmod(v, factor)
                dt = datetime.fromtimestamp(sec, SH)
                shifted = dt.replace(year=dt.year + delta)
                return int(shifted.timestamp()) * factor + rem
            if kind == "text":
                if not isinstance(value, str): raise LabError("FIELD_TYPE_MISMATCH")
                return self.text(value, inverse=inverse)
            if kind == "temporal":
                s = str(value)
                if re.fullmatch(r"\d{8}", s): return self.shift(value, "date", inverse=inverse)
                if re.match(r"\d{4}-\d{2}-\d{2}[T ]", s): return self.shift(s, "datetime", inverse=inverse)
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s): return self.shift(s, "date", inverse=inverse)
                if re.fullmatch(r"\d{14}", s): return self.text(s, inverse=inverse)
                if re.fullmatch(r"\d{10,}", s):
                    # A future timestamp can gain a digit (e.g. milliseconds
                    # overlap the 14-digit compact date-time shape). Require
                    # an explicit epoch_* field rule instead of guessing units.
                    raise LabError("UNCLASSIFIED_TIME_FORMAT", "时间戳须登记epoch_s/ms/us/ns精度")
                if re.fullmatch(r"(?:[01]\d|2[0-3]):?[0-5]\d(?::?[0-5]\d)?(?:\.\d+)?", s): return value
                out = self.text(s, inverse=inverse)
                if out == s: raise LabError("UNCLASSIFIED_TIME_FORMAT")
                return out
            if kind not in {"date", "datetime"}:
                raise LabError("UNCLASSIFIED_FIELD")
            s = str(value)
            # Keep the field's own timezone/year. Converting to Shanghai here
            # would shift a UTC New Year's Eve into a different source year.
            dt = (parse_time(s) if kind == "date" else datetime.fromisoformat(s.replace("Z", "+00:00")))
            new = dt.replace(year=dt.year + delta)
            if kind == "date":
                if re.fullmatch(r"\d{8}", s):
                    out = new.strftime("%Y%m%d")
                    return int(out) if isinstance(value, int) else out
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    raise ValueError()
                return new.date().isoformat()
            # Preserve source precision and separators: replacing only the year
            # avoids rounding nanos or silently introducing/removing a timezone.
            if not re.match(r"\d{4}-\d{2}-\d{2}[T ]", s):
                raise ValueError()
            return f"{new.year:04d}" + s[4:]
        except LabError:
            raise
        except (ValueError, TypeError, OverflowError, KeyError, OSError):
            raise LabError("INVALID_TIME_MAPPING", "日期或时间类型不匹配；闰日不裁剪") from None

    def text(self, value, *, inverse=False):
        def replace(match):
            s = match.group()
            if re.fullmatch(r"\d{14}", s):
                dt = datetime.strptime(s, "%Y%m%d%H%M%S")
                try: return dt.replace(year=dt.year + (-self.years if inverse else self.years)).strftime("%Y%m%d%H%M%S")
                except ValueError: raise LabError("INVALID_TIME_MAPPING") from None
            if re.fullmatch(r"\d{4}", s):
                return str(self.shift(s, "year", inverse=inverse))
            if "Q" in s.upper():
                return self.shift(s, "quarter", inverse=inverse)
            if re.fullmatch(r"\d{4}[-/]\d{2}", s):
                return self.shift(s, "month", inverse=inverse)
            if re.fullmatch(r"\d{6}", s):
                return self.shift(s, "month", inverse=inverse)
            style = "zh" if "年" in s else "slash" if "/" in s else "compact" if "-" not in s else "dash"
            parts = re.findall(r"\d+", s)
            normalized = (f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                          if style != "compact" else s)
            result = str(self.shift(normalized, "date", inverse=inverse))
            if style == "zh":
                d = date.fromisoformat(result)
                return f"{d.year}年{d.month}月{d.day}日"
            return result.replace("-", "/") if style == "slash" else result
        stripped = DATE_TOKEN.sub("", value)
        if re.search(r"(?<![\d.])\d{10,}(?![\d.])", stripped):
            raise LabError("UNCLASSIFIED_TIME_TEXT", "长数字可能为时间戳，需登记精度规则")
        try: return DATE_TOKEN.sub(replace, value)
        except ValueError: raise LabError("INVALID_TIME_MAPPING") from None

    def identifier(self, value):
        """Opaque UUIDs stay stable; AKQuant's registered expiry IDs contain a date."""
        s = str(value)
        if re.fullmatch(r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", s):
            return s
        match = re.fullmatch(r"(expiry:[^:]+:)(\d{4}-\d{2}-\d{2}|\d{8})", s)
        if match: return match[1] + str(self.shift(match[2], "date"))
        if DATE_TOKEN.search(s) or re.search(r"\d{10,19}", s):
            raise LabError("IDENTIFIER_TIME_RULE_REQUIRED")
        return s

    def record(self, value, rules, *, inverse=False):
        if not isinstance(value, dict) or not isinstance(rules, dict):
            raise LabError("INVALID_RECORD")
        unknown = set(value) - set(rules)
        if unknown:
            # Never return unknown source values or infer from a runtime sample.
            raise LabError("UNCLASSIFIED_FIELD", ",".join(sorted(unknown)))
        out = {}
        for key, item in value.items():
            rule = rules[key]
            if item is None:
                out[key] = None
            elif isinstance(rule, dict):
                if rule.get("type") == "array":
                    if not isinstance(item, list):
                        raise LabError("INVALID_RECORD")
                    out[key] = [self.record(x, rule["fields"], inverse=inverse) if isinstance(x, dict)
                                else self.shift(x, rule["items"], inverse=inverse) for x in item]
                else:
                    out[key] = self.record(item, rule.get("fields", rule), inverse=inverse)
            else:
                out[key] = self.shift(item, rule, inverse=inverse)
        return out
