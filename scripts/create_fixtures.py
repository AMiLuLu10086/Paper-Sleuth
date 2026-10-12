"""Create controlled table and scanned-page samples; no published content."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib.styles import getSampleStyleSheet

root = Path(__file__).resolve().parents[1]
output = root / 'test-results' / 'fixtures'
output.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()
doc = SimpleDocTemplate(str(output/'table.pdf'), pagesize=A4)
table = Table([['Water sample','ATZ removal (%)','Reaction time (min)'],['Tap water','45','20'],['River water','30','20']],colWidths=[140,160,160])
table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),1,colors.black),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7edf3')),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
doc.build([Paragraph('Controlled table sample',styles['Title']),Spacer(1,25),Paragraph('Table 1. Synthetic water treatment results. Units are percent and minutes.',styles['Normal']),Spacer(1,12),table,Spacer(1,12),Paragraph('Note: This is a synthetic test fixture, not a published experiment.',styles['Normal'])])
image = Image.new('RGB',(1200,1600),'white')
draw = ImageDraw.Draw(image)
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',32)
lines = ['Controlled scanned PDF sample','This page has no embedded text layer.','Atrazine concentration: 2.5 mg/L.','Reaction time: 20 minutes.','Removal efficiency: 99 percent.','This is a synthetic OCR test, not a published experiment.']
for i,line in enumerate(lines):
    draw.text((80,100+i*110),line,font=font,fill='black')
image.save(output/'scan.pdf','PDF',resolution=145)
print('Created controlled table and scan fixtures.')
