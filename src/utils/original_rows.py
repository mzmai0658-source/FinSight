"""依据已核实单元格坐标和原PDF表格边界提取完整行，不以OCR近似文本充当原文。"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from .original_cells import original_literal
from .financial_row_identity import row_key

@lru_cache(maxsize=256)
def _table_row(path,size,mtime_ns,page_number,anchors,label,raw_value):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        if not 1<=page_number<=len(pdf.pages):return None
        page=pdf.pages[page_number-1];candidates=[]
        for table in page.find_tables():
            for row in table.rows:
                x0,y0,x1,y1=row.bbox
                if not all(x0<=((box[0]+box[2])/2)<=x1 and y0<=((box[1]+box[3])/2)<=y1 for box in anchors):continue
                text=page.crop(row.bbox).extract_text() or ''
                if original_literal(text,row_key(label)) is None or original_literal(text,raw_value,numeric=True) is None:continue
                cells=[page.crop(cell).extract_text() or '' if cell else '' for cell in row.cells]
                if len(cells)<2:continue
                candidates.append(dict(row_text=text,row_bbox=list(row.bbox),column_literals=cells,
                    row_verification='original_pdf_table_boundaries',page=page_number))
        unique={tuple(value['row_bbox']):value for value in candidates}
        return next(iter(unique.values())) if len(unique)==1 else None

def original_table_row(path,source):
    anchors=[]
    if source.original_cell:
        regions=[source.original_cell.value,source.original_cell.row]
    elif source.scope_proof:
        regions=[source.scope_proof.value_region,source.scope_proof.label_region]
    else:return None
    if any(region.page!=source.page for region in regions):return None
    anchors=[tuple(region.bbox) for region in regions]
    stat=Path(path).stat()
    try:return _table_row(str(path),stat.st_size,stat.st_mtime_ns,source.page,tuple(anchors),source.row,source.raw_value)
    except (OSError,ValueError,IndexError):return None
