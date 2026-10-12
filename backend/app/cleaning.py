from __future__ import annotations

import math
import re
from collections import defaultdict


def normalize_text(text: str) -> str:
    # PDF 软连字符用于排版断词；普通连字符（如 photo-charge）保留。
    text = re.sub(r'(?<=\w)\u00ad\s*(?=\w)', '', text)
    text = text.replace('\u00ad', '').replace('\u00a0', ' ')
    text = re.sub(r'(?<=[A-Za-z])-[ \t]*\n(?=[a-z])', '-', text)
    text = re.sub(r'(?<=[\w)])\s*\n\s*(?=[•⁺⁻−])', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def margin_key(text: str) -> str:
    return re.sub(r'\d+', '#', normalize_text(text).lower())


def is_margin(block: dict, ratio: float) -> bool:
    positions = block.get('positions', [])
    return bool(positions) and all(p['bbox'][3] <= p['height'] * ratio or
        p['bbox'][1] >= p['height'] * (1 - ratio) for p in positions)


def clean_blocks(blocks: list[dict], page_count: int, settings) -> tuple[list[dict], list[dict]]:
    repetitions = defaultdict(set)
    for block in blocks:
        if is_margin(block, settings.margin_ratio):
            repetitions[margin_key(block['raw_text'])].update(block['pages'])
    required = max(settings.min_repeat_pages, math.ceil(page_count * settings.repeat_ratio))
    cleaned, log = [], []
    for block in blocks:
        text = normalize_text(block['raw_text'])
        reason = None
        margin = is_margin(block, settings.margin_ratio)
        if block['label'] in ['page_header', 'page_footer']:
            reason = '版面模型识别的页眉或页脚'
        elif margin and re.fullmatch(r'\d{1,4}', text):
            reason = '页边独立页码'
        elif margin and len(repetitions[margin_key(text)]) >= required:
            reason = '跨页重复的页边内容'
        elif margin and any(re.search(pattern, text, re.I) for pattern in settings.margin_patterns):
            reason = '自定义页边清洗规则'
        elif not text and block['kind'] == 'text':
            reason = '空白文本'
        updated = {**block, 'text': text, 'removed': bool(reason), 'cleaning_reason': reason}
        cleaned.append(updated)
        if reason or text != block['raw_text']:
            log.append({'block_id': block['id'], 'pages': block['pages'], 'reason': reason or '修复断行与排版空白',
                        'before': block['raw_text'], 'after': '' if reason else text})
    return cleaned, log
