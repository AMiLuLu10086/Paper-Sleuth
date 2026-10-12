from pathlib import Path
import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
root = Path(__file__).resolve().parents[1]
sample = next(root.glob('*.pdf'))
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=30) as client:
    with sample.open('rb') as stream:
        result = client.post('/api/documents', files={'files': (sample.name, stream, 'application/pdf')})
    result.raise_for_status()
    print(json.dumps(result.json(), ensure_ascii=False), flush=True)
    item = result.json()['results'][0]
    if item.get('error'):
        raise RuntimeError(item['error'])
    if item.get('duplicate'):
        docs = client.get('/api/documents').json()
        job_id = next(d['job_id'] for d in docs if d['id'] == item['document_id'])
    else:
        job_id = item['job_id']
    last = ''
    for _ in range(240):
        job = client.get('/api/jobs/' + job_id).json()
        stage = f'{job["status"]} {job["progress"]:.0%} {job["stage"]}'
        if stage != last:
            print(stage, flush=True)
            last = stage
        if job['status'] == 'failed':
            raise RuntimeError(job['error'])
        if job['status'] == 'complete':
            parsed = client.get('/api/documents/' + item['document_id'] + '/content').json()
            print(json.dumps({'pages':parsed['pages'],'figures':parsed['figures'],'tables':parsed['tables'],
                'cleaning_entries':len(parsed['cleaning_log'])},ensure_ascii=False),flush=True)
            break
        time.sleep(3)
    else:
        raise RuntimeError('Ingestion timed out')
