"""内容解析层：本地文件、网页、视频字幕、全网检索。

所有解析结果统一输出为 Markdown 文本，便于直接落入知识库。
视频转图文 / 图片 OCR 依赖外部二进制（ffmpeg / whisper / tesseract），
放在 Phase 8 补齐，此处仅给出可识别的占位逻辑。
"""
import logging
import os
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger('kb')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}


# ---------------------------------------------------------------------------
# 本地文件解析
# ---------------------------------------------------------------------------
def parse_local_file(file_obj, orig_name, upload_url=''):
    """解析上传的本地文件 -> (title, markdown, meta_dict)。

    file_obj: Django UploadedFile（具备 .read() / .name）。
    """
    ext = Path(orig_name).suffix.lower()
    name = Path(orig_name).stem or '未命名'

    # 防御性复位：调用方可能已消费过流（如先 f.chunks() 落盘），
    # 不复位会导致 .md/.txt/.docx/.pdf/.pptx 等分支读到 EOF 而内容为空。
    try:
        file_obj.seek(0)
    except Exception:
        pass

    if ext in ('.md', '.markdown', '.txt'):
        raw = file_obj.read()
        text = raw.decode('utf-8', errors='ignore')
        return name, text, {'ext': ext}

    if ext == '.docx':
        from docx import Document
        doc = Document(file_obj)
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        md = '\n\n'.join(lines)
        return name, md, {'ext': ext}

    if ext == '.pdf':
        import PyPDF2
        reader = PyPDF2.PdfReader(file_obj)
        pages = [ (p.extract_text() or '') for p in reader.pages ]
        md = '\n\n'.join(pages)
        return name, md, {'ext': ext, 'pages': len(reader.pages)}

    if ext == '.pptx':
        from pptx import Presentation
        prs = Presentation(file_obj)
        blocks = []
        for i, slide in enumerate(prs.slides, 1):
            blocks.append(f'\n## 第 {i} 页')
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    blocks.append(shape.text_frame.text.strip())
        return name, '\n'.join(blocks), {'ext': ext, 'slides': len(prs.slides)}

    if ext == '.doc':
        raise ValueError('旧版 .doc 需要 LibreOffice 转换，当前版本暂不支持，请另存为 .docx。')

    if ext in IMAGE_EXTS:
        # 落盘临时文件后调用 OCR（Tesseract），缺失则给出提示
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        file_obj.seek(0)
        tmp.write(file_obj.read())
        tmp.close()
        from core import media
        ocr_md = ''
        try:
            _, ocr_md = media.ocr_image_to_markdown(tmp.name, name)
        except Exception as e:
            logger.error('ocr error: %s', e)
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        md = (f'![{name}]({upload_url or orig_name})\n\n'
              f'> 图片 OCR 识别结果（已结构化清洗：修正误识、统一格式、过滤无效内容）：\n\n'
              f'{ocr_md or "（OCR 暂不可用，请安装 Tesseract-OCR 并加入 PATH）"}')
        return name, md, {'ext': ext, 'ocr': 'done' if ocr_md else 'unavailable'}

    raise ValueError(f'暂不支持的文件类型：{ext}')


# ---------------------------------------------------------------------------
# 网页解析（普通网页 / 公众号 / 共享链接 / B站图文 等）
# ---------------------------------------------------------------------------
def parse_web_page(url, platform='auto'):
    """抓取网页正文，提取标题与正文段落，返回 (title, markdown)。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
    except requests.exceptions.RequestException as e:
        raise ValueError('网页请求失败（网络/DNS/超时/被拦截）：%s' % str(e)[:160])
    if resp.status_code != 200:
        raise ValueError('网页返回状态码 %s，无法解析（可能为防盗链/反爬/付费墙）。' % resp.status_code)
    resp.encoding = resp.apparent_encoding or 'utf-8'
    html = resp.text
    soup = BeautifulSoup(html, 'html.parser')

    title = soup.title.get_text(strip=True) if soup.title else url
    try:
        from readability import Document
        doc = Document(html)
        body_soup = BeautifulSoup(doc.summary(), 'html.parser')
        title = doc.short_title() or title
    except Exception:
        body_soup = soup

    for tag in body_soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript']):
        tag.decompose()

    lines = []
    for el in body_soup.find_all(['h1', 'h2', 'h3', 'p', 'li']):
        txt = el.get_text(strip=True)
        if not txt:
            continue
        tag = el.name
        if tag in ('h1', 'h2', 'h3'):
            level = '#' * int(tag[1])
            lines.append(f'{level} {txt}')
        else:
            lines.append(txt)

    md = f'# {title}\n\n> 来源：{url}\n> 平台识别：{platform}\n\n' + '\n\n'.join(lines)
    return title, md


# ---------------------------------------------------------------------------
# 全网检索（国内可访问的 Bing 结果解析，失败返回空列表）
# ---------------------------------------------------------------------------
def search_web(query, limit=10):
    try:
        url = 'https://www.bing.com/search?q=' + requests.utils.quote(query)
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []
        for li in soup.select('li.b_algo')[:limit]:
            h = li.find('h2')
            if not h:
                continue
            a = h.find('a')
            results.append({
                'title': h.get_text(strip=True),
                'url': a.get('href') if a else '',
                'snippet': (li.find('p').get_text(strip=True) if li.find('p') else ''),
            })
        return results
    except Exception as e:
        logger.error('search_web failed: %s', e)
        return []
