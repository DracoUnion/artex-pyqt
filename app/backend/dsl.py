"""资产 DSL 查询语法（移植自 db/asset_dsl.go 的实用子集）。

文法（大小写不敏感，隐式 AND）：
  expr      := or_expr
  or_expr   := and_expr ( 'or' and_expr )*
  and_expr  := unary ( 'and' unary )*
  unary     := 'not' unary | '(' expr ')' | factor
  factor    := key ':' value   （value 为裸词或带引号串）

支持的键：
  type/kind | domain | root_domain | ip | url | app | service | service_name
  tech/technology | port | status/status_code | method | company | task |
  name | id | source | host
"""
from __future__ import annotations

import re
from typing import Any

TOK_RE = re.compile(
    r"""
    \s*(
      (?P<lpar>\() | (?P<rpar>\)) |
      (?P<op>\b(?:and|or|not)\b) |
      (?P<key>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*"(?P<quoted>[^"]*)" |
      (?P<key2>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?P<val>[^\s()]+) |
      (?P<word>[^\s()]+)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


class DSLSyntaxError(Exception):
    pass


def tokenize(text: str) -> list:
    toks = []
    pos = 0
    for m in TOK_RE.finditer(text):
        if m.start() != pos and text[pos:m.start()].strip():
            raise DSLSyntaxError(f"无法解析: {text[pos:m.start()]!r}")
        if m.group("lpar"):
            toks.append(("lpar", "("))
        elif m.group("rpar"):
            toks.append(("rpar", ")"))
        elif m.group("op"):
            toks.append(("op", m.group("op").lower()))
        elif m.group("key"):
            toks.append(("factor", (m.group("key").lower(), m.group("quoted") or m.group("val"))))
        elif m.group("word"):
            toks.append(("word", m.group("word")))
        pos = m.end()
    if pos < len(text) and text[pos:].strip():
        raise DSLSyntaxError(f"无法解析尾部: {text[pos:]!r}")
    return toks


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else ("eof", None)

    def next(self):
        t = self.peek()
        self.i += 1
        return t

    def parse(self):
        return self._or()

    def _or(self):
        left = self._and()
        while self.peek()[1] == "or":
            self.next()
            right = self._and()
            left = ("or", left, right)
        return left

    def _and(self):
        left = self._unary()
        while self.peek()[1] in ("and", None):
            if self.peek()[0] == "eof" or self.peek()[1] == "or" or self.peek()[1] == ")":
                break
            if self.peek()[1] == "and":
                self.next()
            right = self._unary()
            left = ("and", left, right)
        return left

    def _unary(self):
        t = self.peek()
        if t[0] == "op" and t[1] == "not":
            self.next()
            return ("not", self._unary())
        if t[0] == "lpar":
            self.next()
            e = self._or()
            if self.peek()[0] != "rpar":
                raise DSLSyntaxError("缺少 )")
            self.next()
            return e
        if t[0] == "factor":
            self.next()
            return ("factor", t[1])
        if t[0] == "word":
            self.next()
            return ("contains", t[1])
        raise DSLSyntaxError(f"意外的 token: {t}")


# ---- 求值 -----------------------------------------------------------------
KEY_ALIASES = {
    "type": "type", "kind": "type",
    "domain": "domain", "root_domain": "root_domain",
    "ip": "ip", "url": "url", "host": "url",
    "app": "app_name", "service": "service_name", "service_name": "service_name",
    "tech": "technologies", "technology": "technologies",
    "port": "port", "status": "status_code", "status_code": "status_code",
    "method": "method", "company": "company", "name": "app_name", "id": "id",
    "source": "company_source", "task": "task_ids",
}


def evaluate(ast, row: dict) -> bool:
    if ast[0] == "or":
        return evaluate(ast[1], row) or evaluate(ast[2], row)
    if ast[0] == "and":
        return evaluate(ast[1], row) and evaluate(ast[2], row)
    if ast[0] == "not":
        return not evaluate(ast[1], row)
    if ast[0] == "contains":
        # 裸词：对常见字段做包含匹配
        hay = " ".join(
            str(row.get(k) or "") for k in ("domain", "ip", "url", "app_name", "service_name", "method")
        ).lower()
        return ast[1].lower() in hay
    if ast[0] == "factor":
        key, value = ast[1]
        field = KEY_ALIASES.get(key, key)
        v = row.get(field)
        if key == "task":
            try:
                tid = int(value)
                return str(tid) in (row.get("task_ids") or "[]")
            except ValueError:
                return False
        if key in ("port", "status", "status_code", "id"):
            if v is None:
                return False
            try:
                return int(v) == int(value)
            except Exception:
                return str(v) == str(value)
        if v is None:
            return False
        if isinstance(v, (list, dict)):
            s = str(v)
        else:
            s = str(v)
        return value.lower() in s.lower()
    return False


def parse_dsl(text: str):
    toks = tokenize(text)
    return Parser(toks).parse()
