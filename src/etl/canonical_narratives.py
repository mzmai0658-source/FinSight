"""从OCR页面提取可检索正文，清除格式标记并保留表格行的内容顺序。"""
import re
from bs4 import BeautifulSoup

def ocr_narrative(page):
    value=page.get('markdown',{}).get('text','') or ''
    if not value.strip():
        value='\n'.join((b.get('block_content') or '') for b in (page.get('prunedResult',{}).get('parsing_res_list') or []) if b.get('block_label') not in {'header','footer','number'})
    value=re.sub(r'!\[[^\]]*\]\([^)]*\)','',value)
    soup=BeautifulSoup(value,'html.parser')
    for table in soup.find_all('table'):
        rows=[' | '.join(cell.get_text(' ',strip=True) for cell in row.find_all(['th','td'])) for row in table.find_all('tr')]
        table.replace_with('\n'+'\n'.join(rows)+'\n')
    text=soup.get_text()
    text=re.sub(r'(?m)^\s*#{1,6}\s*','',text)
    text=text.replace('**','').replace('`','')
    text=re.sub(r'(?m)^\s*\|?(?:\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$','',text)
    text=re.sub(r'[ \t]{2,}',' ',text)
    return re.sub(r'\n{3,}','\n\n',text).strip()
