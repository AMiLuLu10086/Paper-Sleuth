from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5


def make_chunks(blocks: list[dict], title: str, document_id: str, index_id: str, tokenizer, settings) -> list[dict]:
    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False))

    result = []

    def emit(parts: list[str], origins: list[dict], kind: str, section: str, prefix: str):
        text = prefix + '\n\n' + '\n'.join(parts)
        result.append({'id': str(uuid5(NAMESPACE_URL, index_id + ':' + str(len(result)))),
            'document_id': document_id, 'index_id': index_id, 'text': text, 'kind': kind, 'section': section,
            'pages': sorted({page for b in origins for page in b['pages']}),
            'boxes': [p for b in origins for p in b['positions']], 'block_ids': list(dict.fromkeys(b['id'] for b in origins)),
            'token_count': count(text)})

    # 所有标题前缀均计入限制，避免向量模型静默截掉片段尾部。
    def prefix_for(section):
        prefix = title + (f'\n{section}' if section else '')
        ids = tokenizer.encode(prefix, add_special_tokens=False)
        return tokenizer.decode(ids[:min(100, settings.max_tokens // 4)], skip_special_tokens=True)

    section, parts, origins = '', [], []

    def flush(overlap=False):
        nonlocal parts, origins
        if not parts:
            return
        prefix = prefix_for(section)
        emit(parts, origins, 'text', section, prefix)
        if overlap and settings.overlap_tokens:
            # 保留尾段来源，重叠证据的页面与坐标不会误指向下一段。
            budget = min(settings.overlap_tokens, settings.max_tokens - count(prefix) - 8)
            tail = tokenizer.encode(parts[-1], add_special_tokens=False)[-budget:]
            parts, origins = [tokenizer.decode(tail, skip_special_tokens=True)], [origins[-1]]
        else:
            parts, origins = [], []

    for block in blocks:
        if block['removed']:
            continue
        if block['label'] in ['section_header', 'title']:
            flush()
            section = block['text']
            continue
        kind, text = block['kind'], block['text']
        prefix = prefix_for(block.get('section') or section)
        if kind in ['table', 'figure']:
            flush()
            if kind == 'figure':
                if text:
                    ids = tokenizer.encode(text, add_special_tokens=False)
                    budget = settings.max_tokens - count(prefix) - 4
                    for start in range(0, len(ids), budget):
                        emit([tokenizer.decode(ids[start:start+budget], skip_special_tokens=True)], [block], kind, section, prefix)
            else:
                headers = block.get('table_headers', [])
                rows = block.get('table_rows', [])
                header = ' | '.join(headers)
                group = []
                for row in rows:
                    row_text = ' | '.join(row)
                    candidate = '\n'.join([text, header, *group, row_text])
                    if group and count(prefix + '\n\n' + candidate) > settings.max_tokens:
                        emit([text, header, *group], [block], kind, section, prefix)
                        group = []
                    if count(prefix + '\n\n' + text + '\n' + header + '\n' + row_text) > settings.max_tokens:
                        # 单行过长时仍附表头，按内容分段而不是静默截断。
                        fixed = prefix + '\n\n' + text + '\n' + header
                        available = settings.max_tokens - count(fixed) - 4
                        if available < 16:
                            raise ValueError('表格标题/表头过长，请增加 chunking.max_tokens 或人工检查表格')
                        ids = tokenizer.encode(row_text, add_special_tokens=False)
                        for start in range(0, len(ids), available):
                            emit([text, header, tokenizer.decode(ids[start:start+available], skip_special_tokens=True)], [block], kind, section, prefix)
                    else:
                        group.append(row_text)
                if group:
                    emit([text, header, *group], [block], kind, section, prefix)
                elif not rows and text:
                    emit([text], [block], kind, section, prefix)
            continue
        if not text:
            continue
        budget = settings.max_tokens - count(prefix_for(section)) - 4
        ids = tokenizer.encode(text, add_special_tokens=False)
        if len(ids) > budget:
            flush()
            step = max(1, budget - settings.overlap_tokens)
            for start in range(0, len(ids), step):
                emit([tokenizer.decode(ids[start:start+budget], skip_special_tokens=True)], [block], 'text', section, prefix_for(section))
                if start + budget >= len(ids):
                    break
            continue
        if parts and count(prefix_for(section) + '\n\n' + '\n'.join([*parts, text])) > settings.max_tokens:
            flush(overlap=True)
            if count(prefix_for(section) + '\n\n' + '\n'.join([*parts, text])) > settings.max_tokens:
                parts, origins = [], []
        parts.append(text)
        origins.append(block)
    flush()
    if any(c['token_count'] > settings.max_tokens for c in result):
        raise ValueError('切片超过 token 上限，请检查异常表格或 tokenizer')
    return result
