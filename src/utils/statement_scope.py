"""作品说明：按物理顺序核验报表范围。OCR 表名不能单独证明口径；重新读取标题、行名和值的区域，并查找同类报表最近的前置标题。"""
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from .original_cells import normalized_positions,original_literal
from .financial_row_identity import row_key


def physical_caption(text):
    clean=normalized_positions(row_key(text))[0]
    match=re.fullmatch(r'(合并|母公司)(?:年初到报告期末|年初至报告期末|年初至期末)?(资产负债表|利润表|现金流量表)',clean)
    if match:return dict(scope='parent' if match[1]=='母公司' else 'consolidated',family=match[2])
    note=re.fullmatch(r'(合并|母公司)(?:财务|会计)?报表(?:主要)?(?:项目)?(?:附注|注释)',clean)
    if note:return dict(scope='parent' if note[1]=='母公司' else 'consolidated',family='notes')
    if re.fullmatch(r'(?:公司)?主要(?:会计数据(?:和|及)财务指标|会计数据|财务指标|财务数据|财务信息)',clean):
        return dict(scope='consolidated',family='indicators')
    return None


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
                        titles.append(dict(page=number,top=line['bbox'][1],**caption))
    return tuple(titles)


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
    if not identity or identity!={'scope':fact.scope,'family':proof.family}:return False
    if any(hashlib.sha256(text.encode()).hexdigest()!=r.text_sha256 for text,r in ((label,proof.label_region),(value,proof.value_region))):return False
    if original_literal(label,row_key(source.row)) is None:return False
    # 作品说明：跨行小数只在独立验证的单元格中合并，不能跨多列大区域拼接。
    bounded=source.original_cell and value==source.original_cell.value.text==source.raw_value
    if not bounded and original_literal(value,source.raw_value.replace('%',''),numeric=True) is None:return False
    titles=physical_titles(str(path),stat.st_size,stat.st_mtime_ns)
    preceding=[title for title in titles if title['family']==proof.family and
        (title['page']<source.page or title['page']==source.page and title['top']<=proof.value_region.bbox[1]+12)]
    nearest=max(preceding,key=lambda title:(title['page'],title['top'])) if preceding else None
    return bool(nearest and nearest['scope']==fact.scope)
