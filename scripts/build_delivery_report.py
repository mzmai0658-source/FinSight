"""作品说明：将可编辑技术报告和第三方附件生成 PDF，证据摘要应先填写真实最终结果。"""
from __future__ import annotations
import argparse
import html
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def build(output, source_path=None):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Table,TableStyle,Image,KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor,white
    from reportlab.lib.enums import TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    styles={
        'body':ParagraphStyle('body',fontName='STSong-Light',fontSize=10.5,leading=16,spaceAfter=8,wordWrap='CJK',textColor=HexColor('#26384b')),
        'h1':ParagraphStyle('h1',fontName='STSong-Light',fontSize=22,leading=30,spaceAfter=14,keepWithNext=True,textColor=HexColor('#15354d')),
        'h2':ParagraphStyle('h2',fontName='STSong-Light',fontSize=14,leading=20,spaceBefore=12,spaceAfter=8,keepWithNext=True,textColor=HexColor('#15354d')),
        'h3':ParagraphStyle('h3',fontName='STSong-Light',fontSize=12,leading=18,spaceBefore=8,spaceAfter=6,keepWithNext=True,textColor=HexColor('#197c83')),
        'cell':ParagraphStyle('cell',fontName='STSong-Light',fontSize=8,leading=12,wordWrap='CJK',spaceAfter=0),
    }
    def markup(value):
        text=html.escape(value)
        text=re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',r'<link href="\2" color="#197c83">\1</link>',text)
        text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1（\2）',text)
        return text.replace('`','').replace('**','')
    def parse(text):
        lines=text.splitlines();flows=[];index=0
        while index<len(lines):
            line=lines[index].strip();index+=1
            if not line:continue
            # 作品说明：正文连续排版，按内容自然换页；只有第三方附件另起一页。
            if line in ('<!-- page -->','<!-- appendix -->'):continue
            if line=='<!-- architecture -->':
                from reportlab.graphics.shapes import Drawing,Rect,String,Line,Polygon
                diagram=Drawing(495,100)
                labels=[('用户要求','Vue提交'),('结构提案','Qwen理解'),('条件检查','程序编译'),('执行核验','事实/原页'),('回答保存','Java任务')]
                for column,(title,detail) in enumerate(labels):
                    x=column*99
                    diagram.add(Rect(x,35,89,50,rx=6,ry=6,fillColor=HexColor('#e4f0f2'),strokeColor=HexColor('#197c83')))
                    diagram.add(String(x+44.5,64,title,fontName='STSong-Light',fontSize=10,textAnchor='middle',fillColor=HexColor('#15354d')))
                    diagram.add(String(x+44.5,46,detail,fontName='STSong-Light',fontSize=8,textAnchor='middle',fillColor=HexColor('#62758a')))
                    if column<4:
                        diagram.add(Line(x+90,60,x+97,60,strokeColor=HexColor('#197c83')))
                        diagram.add(Polygon([x+94,63,x+98,60,x+94,57],fillColor=HexColor('#197c83'),strokeColor=None))
                diagram.add(String(247.5,14,'MySQL规范事实 · Chroma叙述索引 · PDF版本证据',fontName='STSong-Light',fontSize=9,textAnchor='middle',fillColor=HexColor('#62758a')))
                flows.extend([diagram,Spacer(1,12)]);continue
            picture=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
            if picture:
                path=(ROOT/'docs'/picture.group(2)).resolve()
                if not path.is_relative_to((ROOT/'docs').resolve()):raise ValueError('Report image outside public documents')
                image=Image(str(path));factor=min(495/image.imageWidth,260/image.imageHeight)
                image.drawWidth=image.imageWidth*factor;image.drawHeight=image.imageHeight*factor
                figure=[]
                if flows and isinstance(flows[-1],Paragraph) and flows[-1].style.name in ('h1','h2','h3'):
                    figure.append(flows.pop())
                figure.extend([image,Spacer(1,6),Paragraph(markup(picture.group(1)),styles['cell'])])
                flows.extend([KeepTogether(figure),Spacer(1,10)]);continue
            if line.startswith('|'):
                rows=[line]
                while index<len(lines) and lines[index].strip().startswith('|'):
                    rows.append(lines[index].strip());index+=1
                rows=[r for r in rows if not re.fullmatch(r'[|\s:\-]+',r)]
                cells=[[Paragraph(markup(c.strip()),styles['cell']) for c in r.strip('|').split('|')] for r in rows]
                count=len(cells[0]);width=495/count
                table=Table(cells,colWidths=[width]*count,repeatRows=1,hAlign='LEFT')
                table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#e4f0f2')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,HexColor('#cbd6de')),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
                flows.extend([table,Spacer(1,12)]);continue
            if line.startswith('# '):style='h1';line=line[2:]
            elif line.startswith('## '):style='h2';line=line[3:]
            elif line.startswith('### '):style='h3';line=line[4:]
            else:style='body'
            flows.append(Paragraph(markup(line),styles[style]))
        return flows
    source=(source_path or ROOT/'docs/TECHNICAL_REPORT.md').read_text('utf-8')
    if '{{' in source:raise ValueError('技术报告还有未填写的验收占位符，不能生成冒充完成的 PDF')
    main=source.split('<!-- appendix -->')[0]
    flows=parse(main)
    flows.extend([PageBreak(),Paragraph('附件：第三方资源使用清单',styles['h2'])])
    appendix_text=(ROOT/'docs/THIRD_PARTY.md').read_text('utf-8')
    appendix_text=re.sub(r'^# [^\n]+\n', '', appendix_text, count=1)
    flows.extend(parse(appendix_text))
    def page(canvas,doc):
        canvas.setTitle('FinSight财报证据助手 技术报告');canvas.setAuthor('FinSight')
        canvas.setStrokeColor(HexColor('#197c83'));canvas.setLineWidth(1);canvas.line(50,804,545,804)
        canvas.setFont('STSong-Light',8);canvas.setFillColor(HexColor('#62758a'))
        canvas.drawString(50,25,'FinSight · 阶段性交付 · 2026-10-02');canvas.drawRightString(545,25,f'第 {doc.page} 页')
    output.parent.mkdir(parents=True,exist_ok=True)
    SimpleDocTemplate(str(output),pagesize=A4,rightMargin=50,leftMargin=50,topMargin=58,bottomMargin=48).build(flows,onFirstPage=page,onLaterPages=page)
    from pypdf import PdfReader
    reader=PdfReader(output)
    assert output.stat().st_size<10*1024*1024
    pages=[p.extract_text() for p in reader.pages]
    appendix=next(i+1 for i,text in enumerate(pages) if '附件：第三方资源使用清单' in text)
    assert appendix-1<=15,'技术报告正文超过建议15页'
    print(dict(pdf=str(output),bytes=output.stat().st_size,total_pages=len(pages),main_pages=appendix-1))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'deliverables/20261002/FinSight-技术报告.pdf')
    parser.add_argument('--source',type=Path)
    args=parser.parse_args()
    build(args.output.resolve(),args.source.resolve() if args.source else None)
