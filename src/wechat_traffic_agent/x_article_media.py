"""Original illustrated Article covers, with durable authorship provenance."""
from pathlib import Path
import hashlib
import json
import textwrap


def generate_article_cover(*, title: str, caption: str, folder: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    fonts=[Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
           Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    available=next((p for p in fonts if p.is_file()),None)
    if available is None:
        raise RuntimeError('Article cover font unavailable')
    im=Image.new('RGB',(1500,600),'#102332')
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((1080,100,1430,500),radius=32,fill='#193f52')
    # An original abstract continuity illustration; no invented product screenshot.
    for y,label in [(150,'STATE'),(270,'WORK'),(390,'LEARN')]:
        d.rounded_rectangle((1110,y,1390,y+70),radius=14,outline='#73dece',width=3)
        d.text((1140,y+18),label,font=ImageFont.truetype(str(available),26),fill='#d9f9f2')
    d.line((1250,220,1250,270),fill='#73dece',width=5)
    d.line((1250,340,1250,390),fill='#73dece',width=5)
    font=ImageFont.truetype(str(available),62)
    lines=[]
    line=''
    for char in title:
        if d.textlength(line+char,font=font)>930:
            lines.append(line);line=char
        else:
            line+=char
    if line:lines.append(line)
    if len(lines)>4:
        raise ValueError('Article title is too long for cover')
    d.text((70,60),'FERRO / LIVINGRUNTIME',font=ImageFont.truetype(str(available),26),fill='#73dece')
    for i,line in enumerate(lines):
        d.text((70,155+i*78),line,font=font,fill='#f4f9fb')
    if caption:
        d.text((70,520),caption[:64],font=ImageFont.truetype(str(available),24),fill='#adbbc4')
    path=folder/'x_article_cover.png'
    im.save(path)
    receipt={'status':'GENERATED','kind':'ORIGINAL_GRAPHIC','path':str(path.resolve()),
        'creator':'Ferro','source':'local original graphic renderer','license':'ORIGINAL_WORK',
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'title':title,'caption':caption,
        'width':1500,'height':600,'ai_image_model':False}
    (folder/'x_article_media.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt
