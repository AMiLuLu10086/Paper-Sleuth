"""Verify real local embeddings and reranking through the running API."""
from pathlib import Path
import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding='utf-8',errors='replace')
cases = [
 ('Fe3+ 在这个光催化体系中起什么作用？',[1,2,3,5,6,10]),
 ('加入 Fe3+ 后阿特拉津的去除率和处理时间是多少？',[1,2,3,10]),
 ('不添加 Fe3+ 时阿特拉津去除率是多少？',[2,3,7]),
 ('实验采用什么光源和滤光条件？',[2]),
 ('阿特拉津初始浓度和反应液体积是多少？',[2]),
 ('反应过程中怎样取样和终止自由基反应？',[2]),
 ('研究怎样测定阿特拉津浓度？',[2]),
 ('PMS 浓度使用什么方法测定？',[2,3]),
 ('Fe2+ 浓度使用什么方法测定？',[2]),
 ('实验用什么动力学方程拟合降解过程？',[2]),
 ('比较 PMS、PDS 和 H2O2 时有什么结果？',[3]),
 ('研究如何用 EPR 和猝灭实验确认活性物种？',[4,5,6]),
 ('Fig. 5 所示的反应机理是什么？',[6]),
 ('提高 Fe3+ 浓度到 6 mg/L 有何影响？',[7]),
 ('提高阿特拉津初始浓度对去除效果有什么影响？',[7,8]),
 ('初始 pH 为 3 到 9 时去除效果怎样？',[8]),
 ('初始 pH 为 11 时为何降解效果下降？',[8]),
 ('碳酸根对阿特拉津降解有什么影响？',[8,9]),
 ('自来水和河水中去除率分别是多少？',[10]),
 ('加入 Fe3+ 是否降低 EEO 能耗指标？',[10]),
]
root=Path(__file__).resolve().parents[1]
output=root/'test-results'
output.mkdir(exist_ok=True)
report=[]
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=180) as client:
    documents=client.get('/api/documents').json()
    sample=next(doc for doc in documents if 'atrazine' in doc['title'].lower())
    assert sample['active_index'],'The sample has not completed indexing'
    for question,expected in cases:
        started=time.perf_counter()
        response=client.post('/api/search',json={'question':question,'document_ids':[sample['id']]})
        response.raise_for_status()
        sources=response.json()['sources']
        pages=sorted({page for source in sources for page in source['pages']})
        passed=bool(set(pages)&set(expected))
        entry={'question':question,'expected_pages':expected,'retrieved_pages':pages,'passed':passed,
               'seconds':round(time.perf_counter()-started,3),'top_sources':[{'pages':s['pages'],'kind':s['kind'],'text':s['text'][:160]} for s in sources[:3]]}
        report.append(entry)
        print(json.dumps({k:entry[k] for k in ['question','retrieved_pages','passed','seconds']},ensure_ascii=False),flush=True)
    refusal=client.post('/api/search',json={'question':'Table S1 的各项水质参数是多少？','document_ids':[sample['id']]}).json()
    assert refusal['notice'] and refusal['sources']==[]
    scope=client.post('/api/search',json={'question':'atrazine','document_ids':[]}).json()
    assert scope['sources']==[]
(output/'retrieval-report.json').write_text(json.dumps({'cases':report,'passed':sum(r['passed'] for r in report),
    'total':len(report),'missing_supplement':refusal['notice']},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Retrieval evidence-page coverage: {sum(r["passed"] for r in report)}/{len(report)}',flush=True)
assert all(entry['passed'] for entry in report),'Some evidence-page checks failed; inspect test-results/retrieval-report.json'
