"""作品说明：只解析已定位单元格的精确数值及声明单位。允许单元格内部空白，不跨单元格拼接，也不在整页搜索替代值。"""
import re
from decimal import Decimal,InvalidOperation,localcontext

MONEY_FACTORS={name:Decimal(value) for name,value in {
    '元':'1','千元':'1000','万元':'10000','百万元':'1000000','亿元':'100000000'}.items()}

def cell_decimal(raw):
    value=re.sub(r'\s+','',raw).translate(str.maketrans({'，':',','－':'-','−':'-','（':'(','）':')','％':'%'}))
    value=value.replace(',','').removesuffix('%')
    if re.fullmatch(r'\(\d+(?:\.\d+)?\)',value):value='-'+value[1:-1]
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',value):return None
    try:return Decimal(value)
    except InvalidOperation:return None

def normalize_cell(raw,raw_unit,unit):
    number=cell_decimal(raw)
    if number is None:return None
    if unit=='元':factor=MONEY_FACTORS.get(raw_unit)
    elif unit in {'%','元/股'}:factor=Decimal(1) if raw_unit==unit else None
    else:factor=None
    if factor is None:return None
    with localcontext() as context:
        context.prec=max(50,len(number.as_tuple().digits)+12)
        return number*factor
