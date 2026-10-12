from pathlib import Path
import json
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from backend.app.config import load_settings,prepare_environment
from backend.app.parsing import parse_pdf
from backend.app.cleaning import clean_blocks

settings=load_settings()
prepare_environment(settings)
root=Path(__file__).resolve().parents[1]
for name in ['table','scan']:
    started=time.perf_counter()
    parsed=parse_pdf(root/'test-results'/'fixtures'/f'{name}.pdf',root/'test-results'/f'{name}-parsed',settings)
    parsed['blocks'],parsed['cleaning_log']=clean_blocks(parsed['blocks'],parsed['pages'],settings.cleaning)
    (root/'test-results'/f'{name}-parsed'/'parsed.json').write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding='utf-8')
    text=' '.join(block['text'] for block in parsed['blocks'])
    if name=='table':
        tables=[block for block in parsed['blocks'] if block['kind']=='table']
        assert tables,'Controlled table was not detected'
        assert any('45' in str(block['table_rows']) and '30' in str(block['table_rows']) for block in tables)
        assert any('Reaction' in str(block['table_headers']) for block in tables)
    else:
        assert '2.5' in text and '99' in text and '20' in text,text
    print(json.dumps({'fixture':name,'pages':parsed['pages'],'tables':parsed['tables'],'seconds':round(time.perf_counter()-started,2),'text':text[:600]},ensure_ascii=False),flush=True)
