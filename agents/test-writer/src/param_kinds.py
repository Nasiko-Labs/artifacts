"""Pure: the kind of a parameter (number, float, string, list, dict, bool, date) from its type hint, else its
default's type, else a name heuristic (`*_id`, `amount` -> number; `name`, `url` -> string; `items` -> list)."""

from __future__ import annotations

import re

from code_model import Param

_NUMERIC = re.compile(r"\b(?:int|float|number|decimal|bigint|complex|uint\d*|int\d+|float\d+)\b", re.IGNORECASE)
_FLOAT = re.compile(r"\b(?:float|float\d+|decimal)\b", re.IGNORECASE)
_STRING = re.compile(r"\b(?:str|string)\b", re.IGNORECASE)
_LIST = re.compile(r"\b(?:list|sequence|iterable|tuple|set|array)\b|\[\]|\w\[\]$", re.IGNORECASE)
_DICT = re.compile(r"\b(?:dict|mapping|record|object|map)\b|^\{", re.IGNORECASE)
_BOOL = re.compile(r"\b(?:bool|boolean)\b", re.IGNORECASE)
NULLABLE = re.compile(r"optional|none|null|undefined|^\*", re.IGNORECASE)
DATE = re.compile(r"\b(?:date|datetime|time\.time|Date)\b")
_NAME_NUMERIC = re.compile(r"(?:^|_)(?:id|count|n|num|amount|price|total|qty|quantity|size|limit|age|index|percent|"
                           r"rate|days|hours|minutes|seconds)$|^(?:n|i|x|y|k)$", re.IGNORECASE)
_NAME_STRING = re.compile(r"(?:^|_)(?:name|text|s|str|url|email|title|label|path|key|code|word|line|message)$",
                          re.IGNORECASE)
_NAME_LIST = re.compile(r"(?:^|_)(?:items|values|list|rows|records|lines|names|ids|nums|numbers|xs)$", re.IGNORECASE)


def kind_of(param: Param, family: str) -> str:
    hint, default = param.type_hint, param.default or ""
    if hint:
        for kind, rx in (("bool", _BOOL), ("float", _FLOAT), ("number", _NUMERIC), ("string", _STRING),
                         ("list", _LIST), ("dict", _DICT)):
            if rx.search(hint):
                return kind
        if DATE.search(hint):
            return "date"
    if re.fullmatch(r"-?\d+", default):
        return "number"
    if re.fullmatch(r"-?\d+\.\d*", default):
        return "float"
    if default[:1] in "\"'`" and default:
        return "string"
    if default in ("True", "False", "true", "false"):
        return "bool"
    if default[:1] == "[":
        return "list"
    if default[:1] == "{":
        return "dict"
    name = param.name.lstrip("*.")
    for kind, rx in (("number", _NAME_NUMERIC), ("string", _NAME_STRING), ("list", _NAME_LIST)):
        if rx.search(name):
            return kind
    return ""
