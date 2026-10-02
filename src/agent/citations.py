"""作品说明：引用使用字面原文与章节约束，保留不可变文本。"""
import re


def requested_section(question: str) -> str:
    if not re.search(r'摘引|摘录|引用|原文|逐字', question):
        return ''
    for term in re.findall(r'[“「"]([^”」"]{2,60})[”」"]', question):
        if not re.search(r'20\d{2}|报告|\.pdf', term, re.I):
            return term.strip()
    return ''


def section_matches(section: str, text: str, meta: dict) -> bool:
    title = str(meta.get('section_title') or '')
    title = re.sub(r'^\s*(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十\d]+[、.．])\s*', '', title)
    if title.strip() == section:
        return True
    # 作品说明：正文交叉引用不足以证明章节，需要匹配实际标题行。
    return bool(re.search(r'(?m)^\s*(?:[一二三四五六七八九十\d]+[、.．]\s*)?' + re.escape(section) + r'\s*$', text))


def literal_passage(text: str, section: str = '', limit: int = 900) -> str:
    raw = str(text or '')
    if section:
        heading = re.search(r'(?m)^\s*(?:[一二三四五六七八九十\d]+[、.．]\s*)?' + re.escape(section) + r'\s*$', raw)
        if heading:
            raw = raw[heading.end():].lstrip()
    # 作品说明：仅规范空白字符；原文摘录不由模型重新抄写。
    compact = re.sub(r'\s+', ' ', raw).strip()
    if len(compact) > limit:
        end = max(compact.rfind(mark, 0, limit) for mark in ('。', '！', '？'))
        compact = compact[:end + 1] if end >= 0 else compact[:limit]
    return compact
