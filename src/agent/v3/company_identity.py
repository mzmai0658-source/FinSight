"""公司身份只由已发布目录解析，各层共用同一名称、简称和代码映射。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata


def normalized_text(text):
    return ''.join(unicodedata.normalize('NFKC', str(text)).casefold().split())


@dataclass(frozen=True)
class CompanyMatch:
    text: str
    start: int
    end: int
    codes: tuple[str, ...]


class CompanyDirectory:
    def __init__(self, companies):
        aliases = {}
        for code, names in companies.items():
            names = names if isinstance(names, (list, tuple)) else [names]
            for name in [str(code), *names]:
                registered=normalized_text(name)
                if registered:aliases.setdefault(registered,set()).add(code)
                pending = [normalized_text(name)]
                seen = set()
                while pending:
                    alias = pending.pop()
                    if alias in seen or len(alias) < 2:
                        continue
                    seen.add(alias)
                    aliases.setdefault(alias, set()).add(code)
                    # 法律组织后缀来自统一规则；简称碰撞保留全部候选，不按公司编写问法分支。
                    for suffix in ('股份有限公司', '有限责任公司', '有限公司', '股份', '集团', '公司'):
                        if alias.endswith(suffix) and len(alias) - len(suffix) >= 2:
                            pending.append(alias[:-len(suffix)])
        self.aliases = {alias: tuple(sorted(codes)) for alias, codes in aliases.items()}

    def matches(self, text):
        normalized = []
        positions = []
        for index, char in enumerate(text):
            for part in unicodedata.normalize('NFKC', char).casefold():
                if not part.isspace():
                    normalized.append(part)
                    positions.append(index)
        normalized = ''.join(normalized)
        candidates = []
        for alias, codes in self.aliases.items():
            pattern = re.escape(alias)
            for match in re.finditer(pattern, normalized):
                start, end = positions[match.start()], positions[match.end() - 1] + 1
                if alias.isdecimal() and (start>0 and text[start-1].isdecimal() or end<len(text) and text[end].isdecimal()):
                    continue
                candidates.append(CompanyMatch(text[start:end], start, end, codes))
        return tuple(sorted({item for item in candidates if not any(
            other.start <= item.start and other.end >= item.end and other.end - other.start > item.end - item.start
            for other in candidates)}, key=lambda item: (item.start, -item.end)))

    def codes(self, text):
        return {match.codes[0] for match in self.matches(text) if len(match.codes) == 1}


@lru_cache(maxsize=32)
def _directory(items):
    return CompanyDirectory(dict(items))


def company_directory(companies):
    items = tuple(sorted((str(code), tuple(names) if isinstance(names, (list, tuple)) else str(names))
                         for code, names in companies.items()))
    return _directory(items)


def current_company_codes(text, companies):
    return company_directory(companies).codes(text)
