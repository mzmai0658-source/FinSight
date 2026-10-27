"""只规范字符和数字表示，不根据公司、问题编号或动作词改写业务要求。"""
import re
import unicodedata


DIGITS = dict(zip('零〇○Ｏ一二两三四五六七八九', '00001223456789'))
DIGITS.update(dict(zip('壹贰叁肆伍陆柒捌玖', '123456789')))
UNITS = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000}


def chinese_integer(text):
    if all(char in DIGITS for char in text):
        return int(''.join(DIGITS[char] for char in text))
    total = 0
    digit = 0
    for char in text:
        if char in DIGITS:
            digit = int(DIGITS[char])
        elif char in UNITS:
            total += (digit or 1) * UNITS[char]
            digit = 0
        else:
            raise ValueError('Unsupported numeral')
    return total + digit


def normalize_year_notation(text):
    text = unicodedata.normalize('NFKC', str(text))
    numerals = ''.join(DIGITS) + ''.join(UNITS)
    def replace(match):
        value = chinese_integer(match[0])
        return str(value) if 2000 <= value <= 2100 else match[0]
    token='['+re.escape(numerals)+']{2,12}'
    endpoint='['+re.escape(numerals)+'0-9]{2,12}'
    separator=r'(?:至|到|以及|和|及|与|[-—~～、,])'
    suffix=r'(?=\s*(?:年|(?:'+separator+r'\s*'+endpoint+r'\s*)+年))'
    return re.sub(token+suffix, replace, text)
