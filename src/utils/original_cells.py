"""作品说明：按字面核对原始文本，保留正负号与原始字节依据。"""
import re


def normalized_positions(text,ignore_commas=False,preserve_separators=False):
    characters=[];positions=[]
    for index,char in enumerate(text):
        if char.isspace() and not preserve_separators: continue
        if char.isspace() and preserve_separators:
            # 作品说明：PDF 可能把负号或千位分隔符换行；恢复时仍须保留数值单元格边界。
            following=text[index:].lstrip()
            previous=text[index-1] if index else ''
            if following and following[0].isdigit() and (characters and characters[-1]=='-' or previous in {',','，'}):continue
        char={'，':',','－':'-','−':'-','（':'(','）':')'}.get(char,char)
        if ignore_commas and char==',': continue
        characters.append(char);positions.append(index)
    return ''.join(characters),positions


def original_literal(text,needle,numeric=False):
    normalized,positions=normalized_positions(text,ignore_commas=numeric,preserve_separators=numeric)
    target,_=normalized_positions(needle,ignore_commas=numeric)
    if not target:return None
    for match in re.finditer(re.escape(target),normalized):
        before=normalized[match.start()-1] if match.start() else ''
        after=normalized[match.end()] if match.end()<len(normalized) else ''
        if numeric and (before and before in '0123456789.-(' or after and after in '0123456789.'):
            continue
        if numeric and before.isspace() and normalized[:match.start()].rstrip().endswith(('-','(')):
            continue
        return text[positions[match.start()]:positions[match.end()-1]+1]
    return None
