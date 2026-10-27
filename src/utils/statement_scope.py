"""按物理顺序核验报表范围。OCR 表名不能单独证明口径；重新读取标题、行名和值的区域，并查找同类报表最近的前置标题。"""
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from .original_cells import normalized_positions,original_literal
from .financial_row_identity import row_key


def physical_caption(text):
    clean=normalized_positions(row_key(text))[0]
    clean=re.sub(r'[（(]续[）)]$','',clean)
    clean=re.sub(r'^20\d{2}年(?:度|半年度)?','',clean)
    shared=re.fullmatch(r'合并(?:及|和|与)(?:母公司|公司)(?:年初到报告期末|年初至报告期末|年初至期末)?(资产负债表|利润表|现金流量表)',clean)
    if shared:return dict(scope='mixed',family=shared[1])
    match=re.fullmatch(r'(合并|母公司|公司)(?:年初到报告期末|年初至报告期末|年初至期末)?(资产负债表|利润表|现金流量表)',clean)
    if match:return dict(scope='consolidated' if match[1]=='合并' else 'parent',family=match[2])
    note=re.fullmatch(r'(合并|母公司)(?:财务|会计)?报表(?:主要)?(?:项目)?(?:附注|注释)',clean)
    if note:return dict(scope='parent' if note[1]=='母公司' else 'consolidated',family='notes')
    if re.fullmatch(r'(?:截至报告期末)?(?:公司)?(?:近[一二两三四五六七八九十\d]+年(?:的)?)?'
                   r'主要(?:会计数据(?:和|及)财务指标|会计数据|财务指标|财务数据|财务信息)',clean):
        return dict(scope='consolidated',family='indicators')
    return None


def declared_statement_owner(text):
    """Only explicit preparer fields or standalone legal names declare an owner."""
    clean=normalized_positions(text)[0]
    field=re.fullmatch(r'(?:编制单位|编报单位|编制公司)[:：](.+)',clean)
    if field:
        return re.split(r'20\d{2}年|(?:金额)?单位[:：]',field[1])[0]
    if re.fullmatch(r'[\w\u3400-\u9fff（）()·\-]+(?:股份有限公司|有限责任公司|有限公司)',clean):return clean
    return None


@lru_cache(maxsize=32)
def registered_statement_owners(path,size,mtime):
    """Read exact legal entities attached to original front-matter report titles."""
    import fitz
    owners=set()
    with fitz.open(path) as pdf:
        for page in list(pdf)[:3]:
            lines=[normalized_positions(line)[0] for line in page.get_text().splitlines() if line.strip()]
            for index,line in enumerate(lines):
                title=re.fullmatch(r'(.*?)20\d{2}年?(?:年?度|半年度|第[一二三四]季度|季度)报告(?:全文)?',line)
                if not title:continue
                name=title[1] or (lines[index-1] if index else '')
                owner=declared_statement_owner(name)
                if owner:owners.add(owner)
    return frozenset(owners)


def _statement_owner_status(path,owners):
    if not owners:return 'not_declared'
    stat=Path(path).stat()
    registered=registered_statement_owners(str(path),stat.st_size,stat.st_mtime_ns)
    if len(registered)!=1:return 'unproven'
    return 'confirmed' if set(owners)==set(registered) else 'contradiction'


def packed_caption_company_status(path,context,original):
    owners=[]
    for line in context.splitlines():
        owner=declared_statement_owner(line)
        if owner:
            if original_literal(original,line) is None:return 'unproven'
            owners.append(owner)
    return _statement_owner_status(path,owners)


@lru_cache(maxsize=128)
def _statement_page_lines(path,size,mtime,number):
    from .original_regions import physical_page
    return tuple((line['top'],line['bottom'],line['text']) for line in
                 physical_page(path,size,mtime,number).extract_text_lines(return_chars=False))


def statement_company_status(path,caption,heading=None):
    """Re-read adjacent PDF owner declarations; a shared short prefix proves nothing."""
    stat=Path(path).stat()
    lines=_statement_page_lines(str(path),stat.st_size,stat.st_mtime_ns,caption.page)
    before=sorted((line for line in lines if line[1]<=caption.bbox[1]+1),reverse=True)
    after=sorted(line for line in lines if line[0]>=caption.bbox[3]-1 and
                 (heading is None or heading.page!=caption.page or line[1]<=heading.bbox[1]+1))
    owners=[]
    for sequence in (before,after):
        for _,_,text in sequence:
            owner=declared_statement_owner(text)
            if owner:
                owners.append(owner);break
            clean=normalized_positions(text)[0]
            if re.fullmatch(r'20\d{2}年?(?:度|\d{1,2}月(?:\d{1,2}日)?)?',clean):continue
            if re.search(r'(?:金额)?单位[:：]',clean):continue
            break
    return _statement_owner_status(path,owners)


@lru_cache(maxsize=32)
def physical_titles(path,size,mtime):
    import fitz
    titles=[]
    with fitz.open(path) as pdf:
        for number,page in enumerate(pdf,1):
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    text=''.join(span['text'] for span in line['spans'])
                    if caption:=physical_caption(text):
                        titles.append(dict(page=number,top=line['bbox'][1],bbox=tuple(line['bbox']),**caption))
    return tuple(titles)


def checked_caption_header_boundary(path,caption,heading):
    """跨页标题到表头之间不能越过任何其他真实报表或章节标题。"""
    if caption.page==heading.page:return True
    if caption.page!=heading.page-1:return False
    from .original_regions import physical_tables
    stat=Path(path).stat()
    def contains(outer,inner):
        return (outer[0]-1<=inner[0] and outer[1]-1<=inner[1]
                and inner[2]<=outer[2]+1 and inner[3]<=outer[3]+1)
    owners=[table for table,_ in physical_tables(str(path),stat.st_size,stat.st_mtime_ns,heading.page)
            if contains(table,heading.bbox)]
    for title in physical_titles(str(path),stat.st_size,stat.st_mtime_ns):
        intervening=(title['page']==caption.page and title['top']>caption.bbox[3]+1 or
                     title['page']==heading.page and title['top']<heading.bbox[1]-1)
        if not intervening:continue
        # 本表内首列“主要会计数据”等表头不是新章节，须与外部标题区分。
        if title['page']==heading.page and title['family']=='indicators' and any(
                contains(table,title['bbox']) for table in owners):continue
        return False
    return True


@lru_cache(maxsize=2048)
def scope_rectangle_texts(path,size,mtime,regions):
    from .original_regions import physical_page
    texts=[]
    for number,bbox in regions:
        page=physical_page(path,size,mtime,number)
        if bbox[2]>page.width or bbox[3]>page.height:raise ValueError('Scope region outside physical page')
        texts.append(page.crop(bbox).extract_text() or '')
    return tuple(texts)


def checked_statement_scope(fact,path):
    source=fact.source
    proof=source.scope_proof if source else None
    if proof is None:return False
    if proof.value_region.page!=source.page or proof.label_region.page!=source.page:return False
    regions=(proof.caption,proof.label_region,proof.value_region)
    stat=Path(path).stat()
    caption,label,value=scope_rectangle_texts(str(path),stat.st_size,stat.st_mtime_ns,tuple((r.page,tuple(r.bbox)) for r in regions))
    if caption!=proof.caption.text:return False
    identity=physical_caption(caption)
    if not identity or identity['family']!=proof.family:return False
    if statement_company_status(path,proof.caption,source.original_cell.heading if source.original_cell else None) not in {'confirmed','not_declared'}:return False
    if source.original_cell and not checked_caption_header_boundary(path,proof.caption,source.original_cell.heading):return False
    if identity['scope']=='mixed':
        column=proof.column_scope
        if column is None or column.page>source.page or source.page-column.page>1:return False
        if column.page==source.page and column.bbox[3]>proof.value_region.bbox[1]+1:return False
        if column.page<source.page:
            from .original_regions import aligned_continuation
            cell=source.original_cell
            if cell is None or column.page!=cell.heading.page or not aligned_continuation(
                str(path),stat.st_size,stat.st_mtime_ns,cell.value,cell.heading):return False
        if not (column.bbox[0]<proof.value_region.bbox[2] and column.bbox[2]>proof.value_region.bbox[0]):return False
        column_text=scope_rectangle_texts(str(path),stat.st_size,stat.st_mtime_ns,((column.page,tuple(column.bbox)),))[0]
        if column_text!=column.text:return False
        normalized=normalized_positions(column_text)[0]
        if normalized not in ({'合并'} if fact.scope=='consolidated' else {'母公司','公司'}):return False
        from .original_regions import column_metadata
        _,_,scopes=column_metadata(str(path),stat.st_size,stat.st_mtime_ns,source.page,proof.value_region.bbox)
        nearest=max(scopes,key=lambda item:item[0][1]) if scopes else None
        if nearest and (normalized_positions(nearest[1])[0]!=normalized or
                        not column.bbox[1]-1<=(nearest[0][1]+nearest[0][3])/2<=column.bbox[3]+1):return False
    elif identity['scope']!=fact.scope:return False
    if any(hashlib.sha256(text.encode()).hexdigest()!=r.text_sha256 for text,r in ((label,proof.label_region),(value,proof.value_region))):return False
    if original_literal(label,row_key(source.row)) is None:return False
    # 跨行小数只在独立验证的单元格中合并，不能跨多列大区域拼接。
    bounded=False
    from .original_regions import same_physical_bbox
    if source.original_cell and value==source.original_cell.value.text and same_physical_bbox(proof.value_region.bbox,source.original_cell.value.bbox):
        from .original_regions import physical_tables
        from .financial_numbers import cell_decimal
        matches={cell for _,cells in physical_tables(str(path),stat.st_size,stat.st_mtime_ns,source.page)
                 for cell in cells if same_physical_bbox(proof.value_region.bbox,cell)}
        physical=len(matches)==1
        bounded=physical and cell_decimal(value) is not None and normalized_positions(value,ignore_commas=True)[0]==normalized_positions(source.raw_value,ignore_commas=True)[0]
    if not bounded and original_literal(value,source.raw_value.replace('%',''),numeric=True) is None:return False
    titles=physical_titles(str(path),stat.st_size,stat.st_mtime_ns)
    preceding=[title for title in titles if title['family']==proof.family and
        (title['page']<source.page or title['page']==source.page and title['top']<=proof.value_region.bbox[1]+12)]
    nearest=max(preceding,key=lambda title:(title['page'],title['top'])) if preceding else None
    return bool(nearest and nearest['scope']==identity['scope'])
