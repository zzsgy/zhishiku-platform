"""Read-only previews. Immutable originals and the indexing parser stay untouched."""
import csv
import io
import re
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from core.document_parser import decode_text, parse
from core.services import render_markdown, wrap_tables
from core.storage import media_path, validate_file

READER_VERSION = 'reader-1'
CODE_EXTENSIONS = {'.json', '.log', '.xml', '.yaml', '.yml', '.py', '.js', '.css', '.sql', '.ini', '.cfg'}


def text(raw):
    return decode_text(raw)[0]


def original(book):
    path = media_path(book.file_path) if book.file_path else None
    if not path or not path.is_file() or path.stat().st_size > settings.UPLOAD_MAX_BYTES:
        raise ValueError('原件暂不可用或超过阅读大小限制，请核对文件目录。')
    return path


def safe_link(url):
    value = str(url).strip()
    parsed = urlsplit(value)
    return value if parsed.scheme.lower() in {'http', 'https', 'mailto'} else ''


def docx_image(book, rid):
    from docx import Document
    from PIL import Image
    path = original(book)
    if path.suffix.lower() != '.docx' or not re.fullmatch(r'rId\d{1,8}', rid):
        raise ValueError('图片不存在')
    raw = path.read_bytes()
    validate_file(path, '.docx')
    doc = Document(io.BytesIO(raw))
    part = doc.part.related_parts.get(rid)
    if not part or not part.content_type.startswith('image/'):
        raise ValueError('图片不存在')
    blob = part.blob
    if len(blob) > 16 * 1024**2:
        raise ValueError('图片超过预览大小限制')
    with Image.open(io.BytesIO(blob)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('图片超过预览尺寸限制')
        fmt = image.format
        image.verify()
    types = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp', 'BMP': 'image/bmp'}
    if fmt not in types:
        raise ValueError('此图片格式请在原件中查看')
    return blob, types[fmt]


def docx_html(raw, book):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    doc = Document(io.BytesIO(raw))
    warnings = []
    counters = {}

    def inline(element):
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 't':
            return escape(element.text or '')
        if tag == 'tab':
            return '&#9;'
        if tag in {'br', 'cr'}:
            return '<br>'
        if tag == 'blip':
            rid = element.get(qn('r:embed'))
            if rid and re.fullmatch(r'rId\d{1,8}', rid):
                dimensions = ''
                from PIL import Image
                try:
                    with Image.open(io.BytesIO(doc.part.related_parts[rid].blob)) as image:
                        dimensions = f' width="{image.width}" height="{image.height}"'
                except (KeyError, OSError, ValueError):
                    pass
                return f'<img loading="lazy"{dimensions} src="/bookshelf/book/{book.pk}/image/{rid}/" alt="文档插图">'
            warnings.append('外部链接图片请在原件中查看。')
            return '<span class="reader-fidelity-note">[外部图片]</span>'
        if tag in {'oMath', 'oMathPara'}:
            warnings.append('Word 公式、浮动文本框和复杂布局请对照下载原件。')
            text = ''.join(element.itertext())
            return '<span class="reader-fidelity-note">[公式：' + escape(text) + '；请对照原件]</span>'
        if tag == 'hyperlink':
            rid = element.get(qn('r:id'))
            rel = doc.part.rels.get(rid) if rid else None
            target = safe_link(rel.target_ref) if rel else ''
            content = ''.join(inline(child) for child in element)
            return f'<a href="{escape(target, quote=True)}" rel="noopener noreferrer" target="_blank">{content}</a>' if target else content
        if tag in {'rPr', 'pPr', 'sdtPr', 'instrText', 'del'}:
            return ''
        result = ''.join(inline(child) for child in element)
        if tag == 'r':
            props = element.find(qn('w:rPr'))
            if props is not None:
                for prop, html_tag in [('b', 'strong'), ('i', 'em'), ('u', 'u'), ('strike', 's')]:
                    node = props.find(qn('w:' + prop))
                    if node is not None and node.get(qn('w:val'), '1') not in {'0', 'false', 'none'}:
                        result = f'<{html_tag}>{result}</{html_tag}>'
                vertical = props.find(qn('w:vertAlign'))
                if vertical is not None and vertical.get(qn('w:val')) in {'subscript', 'superscript'}:
                    html_tag = 'sub' if vertical.get(qn('w:val')) == 'subscript' else 'sup'
                    result = f'<{html_tag}>{result}</{html_tag}>'
        return result

    def paragraph(node):
        para = Paragraph(node, doc)
        content = inline(node)
        if not content.strip():
            return '<p class="reader-spacer" aria-hidden="true"></p>'
        style = para.style.name if para.style else ''
        heading = re.search(r'(?:Heading|标题)\s*([1-6])', style, re.I)
        if heading:
            level = heading.group(1)
            return f'<h{level}>{content}</h{level}>'
        if style == 'Title':
            return f'<h1>{content}</h1>'
        num = node.find('.//' + qn('w:numPr'))
        current_style = para.style
        visited = set()
        while num is None and current_style is not None and current_style.style_id not in visited:
            visited.add(current_style.style_id)
            num = current_style.element.find('.//' + qn('w:numPr'))
            current_style = current_style.base_style
        if num is not None:
            ident = num.find(qn('w:numId'))
            level = num.find(qn('w:ilvl'))
            ident = ident.get(qn('w:val')) if ident is not None else '0'
            level = int(level.get(qn('w:val'))) if level is not None else 0
            marker = '•'
            try:
                numbering = doc.part.numbering_part.element
                num_node = numbering.xpath(f'./w:num[@w:numId="{ident}"]')[0]
                abstract = num_node.find(qn('w:abstractNumId')).get(qn('w:val'))
                definition = numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract}"]/w:lvl[@w:ilvl="{level}"]')[0]
                fmt = definition.find(qn('w:numFmt')).get(qn('w:val'))
                start_node = definition.find(qn('w:start'))
                start = int(start_node.get(qn('w:val'))) if start_node is not None else 1
                override = num_node.xpath(f'./w:lvlOverride[@w:ilvl="{level}"]/w:startOverride')
                if override:
                    start = int(override[0].get(qn('w:val')))
                key = (ident, level)
                if fmt != 'bullet':
                    counters[key] = counters.get(key, start - 1) + 1
                    number = counters[key]
                    marker = (chr(96 + (number - 1) % 26 + 1) if fmt == 'lowerLetter' else str(number)) + '.'
            except (IndexError, AttributeError, ValueError, KeyError):
                pass
            level = max(0, min(level, 8))
            return f'<p class="reader-list reader-indent-{level}"><span class="reader-list-marker">{escape(marker)}</span><span>{content}</span></p>'
        return f'<p>{content}</p>'

    def table(node):
        rows = node.findall(qn('w:tr'))
        output = ['<div class="kb-table-wrap"><table><tbody>']
        for row_index, row in enumerate(rows):
            output.append('<tr>')
            column = 0
            for cell in row.findall(qn('w:tc')):
                prop = cell.find(qn('w:tcPr'))
                grid = prop.find(qn('w:gridSpan')) if prop is not None else None
                colspan = max(1, min(100, int(grid.get(qn('w:val'))))) if grid is not None else 1
                merge = prop.find(qn('w:vMerge')) if prop is not None else None
                if merge is not None and merge.get(qn('w:val')) != 'restart':
                    column += colspan
                    continue
                rowspan = 1
                if merge is not None:
                    for next_row in rows[row_index + 1:]:
                        next_col = 0
                        continuation = False
                        for next_cell in next_row.findall(qn('w:tc')):
                            next_prop = next_cell.find(qn('w:tcPr'))
                            next_grid = next_prop.find(qn('w:gridSpan')) if next_prop is not None else None
                            next_merge = next_prop.find(qn('w:vMerge')) if next_prop is not None else None
                            if next_col == column:
                                continuation = next_merge is not None and next_merge.get(qn('w:val')) != 'restart'
                                break
                            next_col += int(next_grid.get(qn('w:val'))) if next_grid is not None else 1
                        if not continuation:
                            break
                        rowspan += 1
                content = ''.join(block(child) for child in cell)
                output.append(f'<td colspan="{colspan}" rowspan="{rowspan}">{content}</td>')
                column += colspan
            output.append('</tr>')
        return ''.join(output) + '</tbody></table></div>'

    def block(node):
        tag = node.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            return paragraph(node)
        if tag == 'tbl':
            return table(node)
        if tag in {'sdt', 'sdtContent'}:
            return ''.join(block(child) for child in node)
        return ''

    body = ''.join(block(node) for node in doc.element.body)
    if doc.element.body.xpath('.//w:txbxContent'):
        warnings.append('浮动文本框位置可能与原件不同，请对照原件。')
    return body, list(dict.fromkeys(warnings))


def preview(book):
    path = original(book)
    raw = path.read_bytes()
    ext = path.suffix.lower()
    validate_file(path, ext)
    warnings = []
    if ext == '.docx':
        html, warnings = docx_html(raw, book)
    elif ext in {'.html', '.htm'}:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text(raw), 'html.parser')
        for tag in soup(['script', 'style', 'iframe', 'object', 'embed', 'form', 'head', 'svg']):
            tag.decompose()
        # The common sanitizer retains structural tags without running uploaded HTML.
        html = render_markdown(str(soup))
    elif ext in {'.md', '.markdown'}:
        html = render_markdown(text(raw))
    elif ext in {'.txt', '.text'}:
        decoded = text(raw).replace('\r\n', '\n').replace('\r', '\n')
        paragraphs = re.split(r'\n\s*\n', decoded)
        html = ''.join('<p class="reader-plain-paragraph">' + escape(p) + '</p>' for p in paragraphs)
    elif ext in {'.csv', '.tsv'}:
        rows = list(csv.reader(io.StringIO(text(raw)), delimiter='\t' if ext == '.tsv' else ','))
        if sum(len(row) for row in rows) > 100_000:
            raise ValueError('表格过大，请下载原件阅读。')
        html = '<table><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table>'
        html = wrap_tables(html)
    elif ext in CODE_EXTENSIONS:
        html = '<pre><code>' + escape(text(raw)) + '</code></pre>'
    elif ext in {'.xlsx', '.pptx'}:
        markdown, metadata = parse(raw, path.name)
        html = render_markdown(markdown)
        warnings = metadata.get('warnings', [])
        if ext == '.xlsx':
            warnings.append('表格以已保存的单元格值阅读，公式计算与图表请对照原件。')
    elif ext == '.doc':
        raise ValueError('这是旧版 Word（DOC）文档。请在 Word 中另存为 DOCX 或 PDF 后加入书架，即可在线阅读；当前原件仍可下载。')
    elif ext == '.rtf':
        from .vendor.striprtf.striprtf import rtf_to_text
        if len(raw) > 4 * 1024**2:
            raise ValueError('RTF 超过正文预览大小限制，请下载原件阅读。')
        decoded = rtf_to_text(raw.decode('latin-1'), errors='strict')
        html = ''.join('<p class="reader-plain-paragraph">' + escape(p) + '</p>' for p in re.split(r'\n\s*\n', decoded))
        warnings.append('RTF 提供文字阅读；样式、图片和公式请对照原件。')
    else:
        raise ValueError('此格式暂不能生成正文预览，请下载原件，或转换为 PDF、DOCX、Markdown、TXT。')
    if len(html) > 8 * 1024**2:
        raise ValueError('正文过长，请下载原件分段阅读。')
    return {'html': html, 'warnings': warnings, 'format': ext.lstrip('.').upper()}
