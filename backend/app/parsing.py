from __future__ import annotations

import html
import json
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader


def inspect_pdf(path: Path, max_pages=500) -> dict:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('PDF 已加密，请先提供未加密的副本')
        pages = len(reader.pages)
        if pages == 0 or pages > max_pages:
            raise ValueError(f'PDF 页数必须在 1–{max_pages} 之间')
        title = str((reader.metadata or {}).get('/Title', '')).strip()
        return {'pages': pages, 'title': title or path.stem}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('无法读取 PDF，文件可能已损坏或不是有效 PDF') from exc


def table_values(item) -> tuple[list[str], list[list[str]]]:
    data = item.data
    grid = [['' for _ in range(data.num_cols)] for _ in range(data.num_rows)]
    header_rows = set()
    for cell in data.table_cells:
        if cell.column_header:
            header_rows.update(range(cell.start_row_offset_idx, cell.end_row_offset_idx))
        # 合并单元格将文字复制到覆盖位置，保留每行独立检索所需的分组标签。
        for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx):
            for col in range(cell.start_col_offset_idx, cell.end_col_offset_idx):
                if row < len(grid) and col < data.num_cols:
                    grid[row][col] = cell.text.strip()
    headers = [' / '.join(dict.fromkeys(grid[row][col] for row in sorted(header_rows) if grid[row][col]))
               or f'列 {col+1}' for col in range(data.num_cols)]
    return headers, [row for i, row in enumerate(grid) if i not in header_rows]


def table_html(headers: list[str], rows: list[list[str]]) -> str:
    head = ''.join(f'<th>{html.escape(value)}</th>' for value in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{html.escape(value)}</td>' for value in row) + '</tr>' for row in rows)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def parse_pdf(path: Path, output: Path, settings, progress=lambda *_: None) -> dict:
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling_core.types.doc import PictureItem, TableItem
    import pypdfium2 as pdfium

    metadata = inspect_pdf(path, settings.pdf.max_pages)
    output.mkdir(parents=True, exist_ok=True)
    options = PdfPipelineOptions()
    artifact_path = settings.model_path / 'docling'
    if artifact_path.exists():
        options.artifacts_path = artifact_path
    options.do_ocr = settings.pdf.ocr
    options.do_table_structure = settings.pdf.table_structure
    options.ocr_options = RapidOcrOptions(lang=[settings.pdf.ocr_language])
    options.accelerator_options = AcceleratorOptions(num_threads=settings.pdf.threads,
        device=AcceleratorDevice(settings.pdf.device))
    # PDFium 后端兼容 Windows 中文安装路径，版面与阅读顺序仍使用 Docling 模型。
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)})
    progress('版面分析、阅读顺序与表格识别（首次运行会下载解析模型）', .15)
    conversion = converter.convert(path)
    if str(conversion.status.value) != 'success':
        raise ValueError(f'PDF 解析未完整成功：{conversion.status.value}；请检查原文或 OCR 配置')
    document = conversion.document
    document.save_as_json(output / 'docling.json')
    blocks, section = [], ''
    pdf = pdfium.PdfDocument(path)

    @lru_cache(maxsize=2)
    def page_image(page_number):
        page = pdf[page_number - 1]
        bitmap = page.render(scale=settings.pdf.image_scale)
        image = bitmap.to_pil().copy()
        bitmap.close()
        page.close()
        return image

    try:
        # iterate_items 的顺序来自版面模型，不按整页的 y 坐标交错排列左右栏。
        for ordinal, (item, _) in enumerate(document.iterate_items()):
            label = item.label.value
            if label in ['caption', 'footnote']:
                # caption 会附着于对应表格/图片，普通 footnote 仍保留。
                if label == 'caption':
                    continue
            positions = []
            for origin in item.prov:
                page = document.pages[origin.page_no]
                width, height = page.size.width, page.size.height
                box = origin.bbox.to_top_left_origin(page_height=height)
                positions.append({'page': origin.page_no, 'bbox': [box.l, box.t, box.r, box.b],
                                  'width': width, 'height': height})
            kind = 'table' if isinstance(item, TableItem) else 'figure' if isinstance(item, PictureItem) else 'text'
            caption = item.caption_text(document) if kind in ['figure', 'table'] else ''
            text = caption if kind == 'figure' else getattr(item, 'text', '')
            if kind == 'table':
                text = caption
            if label in ['section_header', 'title']:
                section = text
            block = {'id': f'b{ordinal}', 'label': label, 'kind': kind, 'raw_text': text,
                     'pages': sorted(set(position['page'] for position in positions)), 'positions': positions,
                     'section': section, 'asset': None}
            if kind == 'table':
                headers, rows = table_values(item)
                block.update(table_headers=headers, table_rows=rows, table_html=table_html(headers, rows))
            if kind in ['figure', 'table'] and positions:
                origin = positions[0]
                scale = settings.pdf.image_scale
                image = page_image(origin['page'])
                coords = [int(round(value * scale)) for value in origin['bbox']]
                coords = [max(0, coords[0]), max(0, coords[1]), min(image.width, coords[2]), min(image.height, coords[3])]
                if coords[2] > coords[0] and coords[3] > coords[1]:
                    asset = f'{block["id"]}.png'
                    image.crop(coords).save(output / asset)
                    block['asset'] = asset
            blocks.append(block)
        progress('保存图片与结构化内容', .48)
        # 孤立图注也必须保留；不能因为模型漏识别图区域而丢掉文字证据。
        referenced_captions = {ref.cref for item in [*document.pictures, *document.tables] for ref in item.captions}
        for item in document.texts:
            if item.label.value == 'caption' and item.self_ref not in referenced_captions:
                origin = item.prov[0] if item.prov else None
                positions = []
                if origin:
                    page = document.pages[origin.page_no]
                    box = origin.bbox.to_top_left_origin(page_height=page.size.height)
                    positions = [{'page': origin.page_no, 'bbox': [box.l, box.t, box.r, box.b],
                                  'width': page.size.width, 'height': page.size.height}]
                blocks.append({'id': 'caption-' + item.self_ref.rsplit('/', 1)[-1], 'label': 'caption',
                    'kind': 'figure', 'raw_text': item.text, 'pages': [origin.page_no] if origin else [],
                    'positions': positions, 'section': '', 'asset': None,
                    'warning': '已识别图注，但未识别完整图区域，请查看原文页面'})
    finally:
        page_image.cache_clear()
        pdf.close()
    return {**metadata, 'blocks': blocks, 'parser': 'docling',
            'figures': sum(b['kind'] == 'figure' for b in blocks), 'tables': sum(b['kind'] == 'table' for b in blocks)}
