"""读取有物理边界的单元格，禁止将整页文本拼成一个金额。"""
from functools import lru_cache
from pathlib import Path
import re

@lru_cache(maxsize=4096)
def original_region_text(path,size,mtime,page,bbox):
    source=physical_page(path,size,mtime,page)
    if bbox[2]>source.width or bbox[3]>source.height:raise ValueError('Original region outside physical page')
    return source.crop(bbox).extract_text() or ''


@lru_cache(maxsize=64)
def physical_page(path,size,mtime,page):
    """只缓存已解析页对象，不保留打开的 PDF 句柄；文件大小及修改时间参与缓存身份，关闭流前完成单页解析。"""
    import pdfplumber
    if page<1:raise ValueError('Original region page outside document')
    with pdfplumber.open(path,pages=[page]) as pdf:
        if len(pdf.pages)!=1:raise ValueError('Original region page outside document')
        source=pdf.pages[0]
        _=source.objects
        return source


@lru_cache(maxsize=128)
def physical_tables(path,size,mtime,page):
    """缓存表格线交点确定的单元格边界，保留跨行科目的完整高度。"""
    source=physical_page(path,size,mtime,page)
    return tuple((tuple(table.bbox),tuple(dict.fromkeys(tuple(cell) for cell in table.cells)))
                 for table in source.find_tables())


def column_heading(text):
    """先识别实际列身份，再与请求期间比较，不能跳过较近的相反表头。"""
    from .financial_numbers import cell_decimal
    clean=re.sub(r'\s+','',text)
    if cell_decimal(clean) is not None:return False
    return bool(re.search(r'(?:19|20)\d{2}年|本(?:报告期|期|年)|上(?:年|期)|期[初末]|年初|半年度|年度|'
                          r'季度|\d{1,2}月\d{1,2}日|增减|同比|增长|变动|比例|比重|比率|占比|[%％]',clean))


def column_role_matches(fact,text):
    if fact.unit not in {'元','元/股'}:return True
    return not re.search(r'[%％]|百分|同比|增减|增长|变动|比例|比重|比率|占比|幅度|百分点',text)


def header_cell(path,size,mtime,page,cell,cells):
    """纯数字年份必须属于表头行，金额恰为年份的财务数据行不能充当表头。"""
    text=original_region_text(path,size,mtime,page,cell)
    if column_heading(text):return True
    clean=re.sub(r'\s+','',text)
    if not re.fullmatch(r'(?:19|20)\d{2}',clean):return False
    middle=(cell[1]+cell[3])/2
    aligned=sorted((other for other in cells if other[1]<=middle<=other[3]),key=lambda box:box[0])
    texts=[re.sub(r'\s+','',original_region_text(path,size,mtime,page,box)) for box in aligned]
    from .financial_row_identity import row_key,ROW_METRICS
    if any(row_key(value) in ROW_METRICS for value in texts if value):return False
    if any(value in {'项目','科目','指标','主要会计数据','财务指标','附注'} for value in texts):return True
    nonempty=[value for value in texts if value]
    return len(nonempty)>=2 and all(re.fullmatch(r'(?:19|20)\d{2}',value) for value in nonempty)


def _contains(outer,inner,tolerance=1.):
    return all(inner[i]>=outer[i]-tolerance if i<2 else inner[i]<=outer[i]+tolerance for i in range(4))


def positive_interval_overlap(a0,a1,b0,b1):
    """相接边界的一次浮点舍入不能把相邻列当成实际重叠。"""
    from math import nextafter,inf,isfinite
    left,right=max(a0,b0),min(a1,b1)
    return isfinite(left) and isfinite(right) and left<right and nextafter(left,inf)<right


def same_physical_bbox(left,right):
    """原表单元格坐标仅容许数据库保存时的一次二进制浮点舍入。"""
    from math import isfinite,ulp
    return len(left)==len(right)==4 and all(type(a) in (int,float) and type(b) in (int,float)
        and isfinite(a) and isfinite(b) and abs(a-b)<=max(ulp(a),ulp(b)) for a,b in zip(left,right))


def column_metadata(path,size,mtime,page,bbox):
    """保留同列全部期间及口径标记，较近的相反标记同样参与核验。"""
    source=physical_page(path,size,mtime,page)
    owner=next(((table,cells) for table,cells in physical_tables(path,size,mtime,page)
                if _contains(table,bbox)),None)
    headers=[];scopes=[]
    if owner:
        table,cells=owner
        for cell in cells:
            if cell[3]>bbox[1]+1 or not positive_interval_overlap(cell[0],cell[2],bbox[0],bbox[2]):continue
            text=original_region_text(path,size,mtime,page,cell)
            if header_cell(path,size,mtime,page,cell,cells):headers.append((cell,text))
            clean=re.sub(r'\s+','',text)
            if clean in {'合并','母公司','公司'}:
                scopes.append((cell,text))
            elif any(marker in clean for marker in ('合并','公司')):
                for word in source.crop(cell).extract_words(x_tolerance=1.5,y_tolerance=2):
                    if word['text'] in {'合并','母公司','公司'}:
                        box=(word['x0']-.2,word['top']-.2,word['x1']+.2,word['bottom']+.2)
                        scopes.append((box,original_region_text(path,size,mtime,page,box)))
        return table,headers,scopes
    # 无框表采用原始字符边界，有框表不能回退到其他表的文字。
    for word in source.extract_words(x_tolerance=1.5,y_tolerance=2):
        if word['bottom']>=bbox[1] or not positive_interval_overlap(word['x0'],word['x1'],bbox[0],bbox[2]):continue
        box=(word['x0']-.25,word['top']-.25,word['x1']+.25,word['bottom']+.25)
        if column_heading(word['text']):headers.append((box,word['text']))
        if word['text'] in {'合并','母公司','公司'}:scopes.append((box,word['text']))
    return None,headers,scopes


def header_binding_conflict(path,size,mtime,value,heading):
    owner,headers,_=column_metadata(path,size,mtime,value.page,value.bbox)
    if heading.page==value.page:
        if owner and not _contains(owner,heading.bbox):return 'physical_header_period_conflict'
        if any(box[1]>=heading.bbox[3]+1 for box,_ in headers):return 'physical_header_period_conflict'
    elif headers:
        return 'physical_header_period_conflict'
    return None


@lru_cache(maxsize=128)
def _unit_declarations(path,size,mtime,number):
    """缓存原件单位声明的位置，单位匹配不能越过较近的声明。"""
    from src.etl.canonical_audit import unit_in
    source=physical_page(path,size,mtime,number)
    found=[]
    for line in source.extract_text_lines(return_chars=False):
        text=line['text']
        if not re.search(r'单位\s*(?:为\s*[:：]?|[:：])',text):continue
        unit=unit_in(text)
        found.append(((line['x0'],line['top'],line['x1'],line['bottom']),text,unit))
    return tuple(found)


def nearest_unit_declaration(path,size,mtime,fact,proof):
    """单位声明只从当前表格行之前或已证明续表的前页表头读取。"""
    caption=fact.source.scope_proof.caption if fact.source.scope_proof else None
    locations=[(proof.value.page,proof.value.bbox[1])]
    if proof.heading.page!=proof.value.page:locations.append((proof.heading.page,proof.heading.bbox[3]))
    # 前页末尾的真实标题与其后单位，可对应下一页才开始的表头。
    from .statement_scope import checked_caption_header_boundary
    if (caption and caption.page==proof.heading.page-1 and caption.page!=proof.value.page
        and checked_caption_header_boundary(path,caption,proof.heading)):
        locations.append((caption.page,physical_page(path,size,mtime,caption.page).height))
    for number,upper in locations:
        lower=caption.bbox[1] if caption and caption.page==number else 0
        found=[item for item in _unit_declarations(path,size,mtime,number)
               if lower<=item[0][1] and item[0][3]<=upper+1]
        if found:
            box,text,unit=max(found,key=lambda item:item[0][1])
            return number,box,text,unit
    return None


def aligned_continuation(path,size,mtime,value,heading):
    """前页表头只可用于跨物理页且列边界一致的连续表格。"""
    if heading.page!=value.page-1:return False
    before=physical_page(path,size,mtime,heading.page)
    after=physical_page(path,size,mtime,value.page)
    tolerance=2.
    def contains(outer,inner):
        return all((inner[i]>=outer[i]-tolerance if i<2 else inner[i]<=outer[i]+tolerance) for i in range(4))
    for current,cells in physical_tables(path,size,mtime,value.page):
        if not contains(current,value.bbox) or current[1]>after.height*.25:continue
        current_columns=[cell for cell in cells if abs(cell[0]-value.bbox[0])<tolerance and
                         abs(cell[2]-value.bbox[2])<tolerance]
        if not current_columns:continue
        for previous,old_cells in physical_tables(path,size,mtime,heading.page):
            if previous[3]<before.height*.70 or not contains(previous,heading.bbox):continue
            if abs(previous[0]-current[0])>tolerance or abs(previous[2]-current[2])>tolerance:continue
            if not any(abs(cell[0]-value.bbox[0])<tolerance and abs(cell[2]-value.bbox[2])<tolerance
                       for cell in old_cells):continue
            return True
    return False


def generic_period_column(text):
    """通用当期列只表明相对期间，不能单独证明报告年份及季度。"""
    clean=re.sub(r'\s+','',text)
    return bool(re.fullmatch(r'(?:本期(?:发生额)?|本报告期(?:发生额)?|当期(?:发生额)?|当前期(?:发生额)?|'
                             r'本报告期末|本期末|当前期末|期末(?:余额)?)',clean))


@lru_cache(maxsize=256)
def report_period_region(path,size,mtime,code,company,year,period):
    """只从原件前至多三物理页核对公司和报告期，缓存身份含原件大小及修改时间。"""
    import fitz
    from src.agent.v3.contracts import OriginalRegion
    from .report_identity import original_report_identity
    report=dict(stock_code=code,company=company,year=year,period=period)
    with fitz.open(path) as pdf:
        count=min(3,len(pdf))
    for number in range(1,count+1):
        page=physical_page(path,size,mtime,number)
        box=(0.,0.,page.width,page.height)
        text=original_region_text(path,size,mtime,number,box)
        identity=original_report_identity(report,text)
        if identity['company'] and identity['period']:
            return OriginalRegion(page=number,bbox=box,text=text)
    return None


def _cumulative_statement_period(path,size,mtime,fact,proof):
    """三季报流量的通用当期列，须由该张原表标题或表头前正文明确证明年初累计。"""
    caption=fact.source.scope_proof.caption if fact.source.scope_proof else None
    if caption is None:return False
    text=original_region_text(path,size,mtime,caption.page,tuple(caption.bbox))
    if caption.page==proof.heading.page and caption.bbox[1]<proof.heading.bbox[1]:
        page=physical_page(path,size,mtime,caption.page)
        text=original_region_text(path,size,mtime,caption.page,
                                 (caption.bbox[0],caption.bbox[1],page.width,proof.heading.bbox[1]))
    clean=re.sub(r'\s+','',text)
    years={int(value) for value in re.findall(r'(?<!\d)(20\d{2})(?=年)',clean)}
    if years and years!={fact.year}:return False
    return any(marker in clean for marker in ('年初到报告期末','年初至报告期末','年初至期末','前三季度',
                                              '1-9','1—9','1－9','1至9')) or bool(re.search(
        r'1月1日(?:至|到|[-—－~～])(?:20\d{2}年)?9月30日',clean))


def _checked_generic_period(path,fact,proof,heading):
    if path is None:return False
    stat=Path(path).stat()
    cover=report_period_region(str(path),stat.st_size,stat.st_mtime_ns,
                               fact.stock_code,fact.company,fact.year,fact.period)
    if cover is None:return False
    from src.agent.v3.catalog import PRIMARY_FAMILIES
    stock=PRIMARY_FAMILIES.get(fact.metric)=='balance'
    end=bool(re.fullmatch(r'(?:本报告期末|本期末|当前期末|期末(?:余额)?)',heading))
    if stock!=end:return False
    if fact.period in {'Q1','HY'} or stock:return True
    return _cumulative_statement_period(str(path),stat.st_size,stat.st_mtime_ns,fact,proof)

def checked_regions(path,proof):
    stat=Path(path).stat()
    regions=[proof.value,proof.row,proof.heading,proof.context]
    if proof.scope_context:regions.append(proof.scope_context)
    if any(original_region_text(str(path),stat.st_size,stat.st_mtime_ns,r.page,tuple(r.bbox))!=r.text for r in regions):return False
    value,row,heading=proof.value,proof.row,proof.heading
    if value.page!=row.page or abs(value.bbox[1]-row.bbox[1])>1 or abs(value.bbox[3]-row.bbox[3])>1:return False
    if row.bbox[2]>value.bbox[0]+1:return False
    if not (heading.bbox[0]<value.bbox[2] and heading.bbox[2]>value.bbox[0]):return False
    if heading.page>value.page or value.page-heading.page>1:return False
    if heading.page==value.page and heading.bbox[3]>value.bbox[1]+1:return False
    if heading.page<value.page and not aligned_continuation(str(path),stat.st_size,stat.st_mtime_ns,value,heading):return False
    if header_binding_conflict(str(path),stat.st_size,stat.st_mtime_ns,value,heading):return False
    return True

def checked_cell_binding(fact,proof,path=None):
    """依据原件检查口径、期间和单位坐标标注。"""
    from src.utils.original_cells import normalized_positions
    compact=lambda value:normalized_positions(value)[0].replace('／','/')
    if fact.source.page!=proof.value.page:return False
    label=compact(proof.row.text);heading=compact(proof.heading.text)
    context=compact(proof.context.text)
    from src.utils.disclosed_rates import checked_disclosed_rate
    rate=fact.metric.endswith('_reported_yoy') or fact.metric.endswith('_reported_change_vs_year_end')
    if not column_role_matches(fact,heading+fact.source.column+proof.value.text):return False
    from src.utils.financial_numbers import cell_decimal
    same_number=lambda a,b:normalized_positions(a,ignore_commas=True)[0]==normalized_positions(b,ignore_commas=True)[0]
    if cell_decimal(proof.value.text) is None or not same_number(fact.source.raw_value,proof.value.text):return False
    from src.utils.financial_row_identity import row_key,checked_period_column
    if compact(row_key(fact.source.row))!=compact(row_key(label)):return False
    expected=compact(fact.source.column)
    if fact.source.scope_proof and fact.source.scope_proof.column_scope:
        marker=compact(fact.source.scope_proof.column_scope.text)
        expected=expected.replace(marker,'')
        heading=heading.replace(marker,'')
    if expected!=heading or not checked_period_column(fact):return False
    if rate:
        if not checked_disclosed_rate(fact):return False
        scope=compact(proof.scope_context.text) if proof.scope_context else ''
        if fact.scope!='consolidated' or not any(w in scope for w in ('主要会计数据和财务指标','主要会计数据及财务指标')):return False
    else:
        markers={'Q3':('年初至报告期末','前三季度','1-9','1—9','1－9','9月30日'),
                 'Q1':('本报告期','第一季度','一季度','1-3','1—3','1－3','3月31日'),
                 'HY':('半年度','上半年','1-6','1—6','1－6','6月30日','本期'),
                 'FY':(str(fact.year),'本期','本年')}
        if fact.period in {'Q1','Q3','HY'} and generic_period_column(heading):
            if path is None:
                from src.agent.v3.evidence import source_pdf
                path=source_pdf(fact.source)
            if not _checked_generic_period(path,fact,proof,heading):return False
        elif not any(w in heading for w in markers[fact.period]):return False
        if any(w in heading for w in ('同比','增减','上年','上期')):return False
    if fact.unit=='元':
        from src.etl.canonical_audit import row_unit,unit_in
        physical_unit=row_unit(label,label)
        claimed_unit=row_unit(fact.source.row,fact.source.row)
        if claimed_unit and claimed_unit!=physical_unit:return False
        if not physical_unit:
            if path is None:
                from src.agent.v3.evidence import source_pdf
                path=source_pdf(fact.source)
            if path is not None:
                stat=Path(path).stat()
                nearest=nearest_unit_declaration(str(path),stat.st_size,stat.st_mtime_ns,fact,proof)
                if nearest and (nearest[3] is None or nearest[3][0]!=fact.source.raw_unit):return False
        disclosed=physical_unit or unit_in(context)
        if not disclosed or disclosed[0]!=fact.source.raw_unit:return False
    if fact.unit=='%' and '%' not in label+proof.value.text+(heading if rate else ''):return False
    if fact.unit=='元/股' and '元/股' not in label+context:return False
    if fact.scope=='parent':
        from src.utils.statement_scope import physical_caption
        identity=physical_caption(fact.source.scope_proof.caption.text) if fact.source.scope_proof else None
        if not identity or identity['scope'] not in {'parent','mixed'}:return False
    if fact.metric=='main_business_revenue':
        scope=compact(proof.scope_context.text) if proof.scope_context else ''
        if not re.search(r'合并财务报表项目(?:注释|附注)',scope) or label!='主营业务':return False
    return True
