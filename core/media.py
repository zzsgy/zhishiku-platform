"""媒体处理：视频转图文（下载→音频→whisper 转写→关键帧）与图片 OCR。

依赖外部二进制：ffmpeg（音视频/截图）、Tesseract（OCR）、openai-whisper（转写）。
所有函数均做依赖检测，缺失时抛出清晰异常，由调用方降级处理。
"""
import os
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger('kb')

FFMPEG_BIN = (
    os.environ.get('FFMPEG_BIN')
    or shutil.which('ffmpeg')
    or r'C:\ZSK\XiTong\bin\ffmpeg.exe'
)

TESSERACT_BIN = (
    os.environ.get('TESSERACT_BIN')
    or shutil.which('tesseract')
    or r'C:\Program Files\Tesseract-OCR\tesseract.exe'
)


def have_ffmpeg():
    return os.path.exists(FFMPEG_BIN) or shutil.which('ffmpeg') is not None


def have_tesseract():
    return os.path.exists(TESSERACT_BIN) or shutil.which('tesseract') is not None


def have_whisper():
    try:
        import whisper  # noqa: F401
        return True
    except Exception:
        return False


def _ensure_ffmpeg():
    if not have_ffmpeg():
        raise RuntimeError(
            '未检测到 ffmpeg，视频与截图功能不可用。请将 ffmpeg.exe 放到 '
            'C:\\ZSK\\XiTong\\bin\\ 或加入系统 PATH。')


def extract_keyframes(video_path, out_dir, max_frames=6):
    """用 ffmpeg 抽取关键帧，返回图片路径列表。

    max_frames 在此作为"抽帧间隔（秒）"，即每 N 秒取一帧，避免短视频过度抽帧。
    """
    _ensure_ffmpeg()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / 'frame_%03d.jpg'
    interval = max(1, int(max_frames))
    subprocess.run(
        [FFMPEG_BIN, '-y', '-i', str(video_path), '-vf', f'fps=1/{interval}', str(pattern)],
        check=True, capture_output=True,
    )
    return sorted(out_dir.glob('*.jpg'))


def video_to_markdown(url, work_root):
    """视频转图文主流程。返回 (title, markdown)。"""
    _ensure_ffmpeg()
    import yt_dlp
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)

    with yt_dlp.YoutubeDL(
        {'outtmpl': str(work / '%(id)s.%(ext)s'), 'format': 'best', 'quiet': True}
    ) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title') or '视频'
        vid = info.get('id')
        video_file = None
        for ext in ('mp4', 'webm', 'mkv', 'mov', 'm4v'):
            cand = list(work.glob(f"{vid}.{ext}")) if vid else []
            if cand:
                video_file = cand[0]
                break
        if not video_file:
            cands = [p for p in work.iterdir()
                     if p.suffix in ('.mp4', '.webm', '.mkv', '.mov', '.m4v')]
            video_file = cands[0] if cands else None
    if not video_file:
        raise RuntimeError('未能定位下载的视频文件')

    audio = work / 'audio.wav'
    subprocess.run(
        [FFMPEG_BIN, '-y', '-i', str(video_file), '-vn', '-acodec', 'pcm_s16le', str(audio)],
        check=True, capture_output=True,
    )

    transcript = ''
    if have_whisper():
        try:
            import whisper
            model = whisper.load_model('base')
            transcript = model.transcribe(str(audio))['text']
        except Exception as e:
            logger.error('whisper failed: %s', e)
            transcript = f'[whisper 转写失败：{e}]'
    else:
        transcript = '[whisper 未安装，跳过语音转写；已提取音频与关键帧]'

    frames = []
    try:
        frames = extract_keyframes(video_file, work / 'frames')
    except Exception as e:
        logger.error('keyframes failed: %s', e)

    lines = [
        f'# {title}', '', f'> 来源：{url}', '',
        '## 语音 / 字幕转写', transcript or '（无）', '', '## 重点截图',
    ]
    lines += [f'![]({f.name})' for f in frames] or ['（无截图）']
    return title, '\n'.join(lines)


def ocr_image(image_path):
    """图片 OCR，返回识别文本（空串表示不可用）。"""
    if not have_tesseract():
        raise RuntimeError('未检测到 Tesseract，OCR 不可用。请安装 Tesseract-OCR 并加入 PATH。')
    try:
        import pytesseract
        from PIL import Image
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_BIN
        try:
            return pytesseract.image_to_string(Image.open(image_path), lang='chi_sim+eng')
        except Exception:
            # 中文包缺失时退化为仅英文识别，保证基础可用
            return pytesseract.image_to_string(Image.open(image_path), lang='eng')
    except Exception as e:
        logger.error('ocr failed: %s', e)
        return ''


# ---------------------------------------------------------------------------
# OCR 结果清洗与结构化
# ---------------------------------------------------------------------------
import re
import unicodedata

# 常见 OCR 误识字符映射（全角/乱码 -> 正确字符）
_OCR_CHAR_FIX = {
    '（': '(', '）': ')', '【': '[', '】': ']', '“': '"', '”': '"',
    '‘': "'", '’': "'", '；': ';', '：': ':', '，': ',', '。': '.',
    '！': '!', '？': '?', '、': ',', '０': '0', '１': '1', '２': '2',
    '３': '3', '４': '4', '５': '5', '６': '6', '７': '7', '８': '8',
    '９': '9', '—': '-', '─': '-', '·': '·', '．': '.', '　': ' ',
    '\u200b': '', '\ufeff': '',
}

# 标题/列表/键值的启发式正则
_RE_HEADING = re.compile(r'^(第?[0-9]+[.、)）]|[一二三四五六七八九十]+[.、]?|[（(][0-9]+[)）]|#{1,6}\s)')
_RE_LIST = re.compile(r'^([\-•·*‣◦▪▸▶◆●○]\s+|[0-9]+[.、)）]\s+|[（(][0-9]+[)）]\s+|[a-zA-Z][.、)）]\s+)')
_RE_KV = re.compile(r'^(.{1,24}?)[:：]\s*(.+)$')
_RE_BLANK = re.compile(r'^\s*$')
# 仅由符号/标点/空白组成的无效行
_RE_GARBAGE = re.compile(r'^[\s\W_]+$')
# 单字符标点噪声
_RE_LONELY_PUNCT = re.compile(r'^[\s,.;:!?，。；：！？、…—\-_=+*~#@$%^&()\[\]{}<>|/\\"\']+$')


def _fix_chars(line):
    out = []
    for ch in line:
        if ch in _OCR_CHAR_FIX:
            out.append(_OCR_CHAR_FIX[ch])
        else:
            out.append(ch)
    return ''.join(out)


def _is_cjk(ch):
    return '一' <= ch <= '鿿'


def _content_ratio(s):
    """行内『有意义字符』（中文 + 字母数字）占比，用于判断噪声行。"""
    if not s:
        return 0
    meaningful = sum(1 for ch in s if _is_cjk(ch) or ch.isalnum())
    return meaningful / len(s)


def _line_valid(line):
    """过滤无效行：空行、纯符号、纯标点噪声、低内容占比的乱码行。"""
    s = line.strip()
    if not s:
        return False
    if _RE_GARBAGE.match(s):
        return False
    if _RE_LONELY_PUNCT.match(s):
        return False
    # 过滤孤立单字且为非中文（多为噪点）
    if len(s) <= 1 and not _is_cjk(s):
        return False
    # 内容字符占比过低（多为符号 / 噪点，即使混了少量中文）则过滤
    if _content_ratio(s) < 0.25:
        return False
    return True


_RE_CJK = re.compile(r'[\u4e00-\u9fff]')


def _merge_cjk_spaces(line):
    """Tesseract(chi_sim) 常在相邻汉字间插入空格（如『会 议 纪 要』）。
    仅移除『汉字-空格-汉字』之间的空格，保留英文单词内部空格。
    用前瞻性断言避免链式漏匹配（相邻配对消费后导致后续脱节）。"""
    return re.sub(r'([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', r'\1', line)


def _normalize_text(raw):
    """统一换行、去除控制字符、清理行首尾空白，并合并汉字间多余空格。"""
    if not raw:
        return []
    # 兼容 \r\n / \r
    text = raw.replace('\r\n', '\n').replace('\r', '\n')
    # 去掉控制字符（保留正常换行/制表）
    text = ''.join(
        ch for ch in text
        if ch in '\n\t ' or not unicodedata.category(ch).startswith('C')
    )
    lines = [_merge_cjk_spaces(_fix_chars(ln)).strip() for ln in text.split('\n')]
    return lines


def clean_ocr_text(raw):
    """清洗 Tesseract 原始输出，并返回结构化结果。

    处理项：
      - 修正常见 OCR 误识字符（全角/乱码 -> 规范字符）；
      - 过滤空行、纯符号行、孤立噪声标点；
      - 合并被错误断行拆散的段落（行不以句末标点结尾且下一行非空则并入）；
      - 识别标题 / 列表 / 键值 / 段落四种结构；
      - 统一字段格式，输出可直接使用的 Markdown 与结构化块。

    返回 dict：
      {
        'clean':    清洗后的纯文本（段落以空行分隔），
        'markdown': 结构化 Markdown，
        'blocks':   [{'type','text'}...]，
        'stats':    {'raw_lines','valid_lines','titles','lists','kvs','paragraphs'},
      }
    """
    raw_lines = _normalize_text(raw)
    # 第一遍：过滤无效行并去相邻重复
    kept = []
    seen_last = None
    for ln in raw_lines:
        if not _line_valid(ln):
            continue
        if ln == seen_last:
            continue
        kept.append(ln)
        seen_last = ln

    # 第二遍：结构化分块 + 段落续接
    # 策略：标题/列表/键值作为独立结构块；连续段落片段仅在上一片段未以句末
    # 标点收尾时续接，避免把结构行错误并入同一段。首个短片段判定为标题。
    END_PUNCT = set('。.！!？?；;：:，,）)】]…')
    blocks = []          # 每项 {'type','text'[,'key','value']}
    para_buf = []        # 段落续行缓冲（list[str]）

    def _flush_para():
        if not para_buf:
            return
        text = ''.join(para_buf)  # 中文续行无需空格
        if not blocks and len(text) <= 30 and text[-1] not in END_PUNCT:
            blocks.append({'type': 'title', 'text': text})
        else:
            blocks.append({'type': 'paragraph', 'text': text})
        para_buf.clear()

    for ln in kept:
        if _RE_HEADING.match(ln) or _RE_LIST.match(ln) or _RE_KV.match(ln):
            _flush_para()
            if _RE_LIST.match(ln):
                blocks.append({'type': 'list', 'text': ln})
            elif _RE_KV.match(ln):
                m = _RE_KV.match(ln)
                blocks.append({'type': 'kv', 'text': ln,
                               'key': m.group(1).strip(), 'value': m.group(2).strip()})
            else:
                blocks.append({'type': 'heading', 'text': ln})
        else:
            # 段落续接：仅当缓冲非空且上一片段未以句末标点收尾
            if para_buf and para_buf[-1][-1] not in END_PUNCT:
                para_buf.append(ln)
            else:
                _flush_para()
                para_buf = [ln]
    _flush_para()

    # 组装 Markdown
    md_parts = []
    for b in blocks:
        if b['type'] == 'title':
            md_parts.append(f'# {b["text"]}')
        elif b['type'] == 'heading':
            md_parts.append(f'## {b["text"]}')
        elif b['type'] == 'list':
            md_parts.append(f'- {b["text"]}')
        elif b['type'] == 'kv':
            md_parts.append(f'- **{b["key"]}**：{b["value"]}')
        else:
            md_parts.append(b['text'])
    markdown = '\n\n'.join(md_parts)

    clean_text = '\n\n'.join(b['text'] for b in blocks if b['type'] in ('paragraph', 'title', 'heading'))

    stats = {
        'raw_lines': len([l for l in raw_lines if l.strip()]),
        'valid_lines': len(kept),
        'titles': sum(1 for b in blocks if b['type'] == 'title'),
        'headings': sum(1 for b in blocks if b['type'] == 'heading'),
        'lists': sum(1 for b in blocks if b['type'] == 'list'),
        'kvs': sum(1 for b in blocks if b['type'] == 'kv'),
        'paragraphs': sum(1 for b in blocks if b['type'] == 'paragraph'),
    }
    return {'clean': clean_text, 'markdown': markdown, 'blocks': blocks, 'stats': stats}


def ocr_image_to_markdown(image_path, title='图片OCR'):
    """OCR 图片 -> (title, 结构化 Markdown)。无 Tesseract 时抛 RuntimeError。"""
    raw = ocr_image(image_path)
    if not raw or not raw.strip():
        return title, ''
    data = clean_ocr_text(raw)
    return title, data['markdown']
