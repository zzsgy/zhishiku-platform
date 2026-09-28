# -*- coding: utf-8 -*-
"""网页正文解析：以「保真」为第一目标。

做法参考 zzsgy/zhixu-learning 的 ``desktop/lib/article-parser.mjs``，按本站形态
（Django + Markdown 存储 + python-markdown 渲染）重写为 Python。四条主干：

一、编码判定（顺序即优先级，绝不反向覆盖）
    ``Content-Type`` 里的 charset → ``<meta charset>`` → 内容探测 → utf-8。
    背景：requests 在响应头缺 charset 时会把 ``encoding`` 设成 ISO-8859-1；
    而内容探测（chardet）对中文页面并不可靠——实测微信公众号页被判为
    ``Windows-1254``。旧实现写的是 ``resp.encoding = resp.apparent_encoding``，
    等于用探测结果去覆盖响应头里明确的 ``charset=UTF-8``，中文必然烂成乱码。

二、正文定位（正文容器优先，Readability 只兜底）
    公众号走 ``#js_content``；技术文档站走 ``article/main/文档框架正文根``；
    两者都没有才用 Readability。而且兜底结果必须再过一道「结构完整性」检查：
    代码块 / 图片 / 表格 / 列表的数量不得少于正文容器。Readability 为了「干净」
    会把这些结构删掉——这正是旧实现产出 0 个代码块、0 张图、0 行表格的直接原因。

三、HTML → Markdown（与所见即所得编辑器的序列化规范严格一致）
    标题层级、嵌套列表（每层缩进 4 空格）、围栏代码（带语言）、GFM 表格
    （含对齐分隔行）、图片（含 ``data-src`` 等懒加载属性）、链接、引用、行内样式、
    硬换行。产出的 Markdown 交给 ``core/services.render_markdown`` 渲染，与编辑器
    共用同一套语法约定，因此解析入库的内容在阅读页与编辑态都长一样。

四、取回护栏
    体积上限、重定向上限、超时、瞬时故障重试、本机/链路本地地址防护。
"""
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 解压后的 HTML 上限。长技术教程（Jupyter/Quarto 导出）带大量高亮样式，
# gzip 很小但解压后可超 5 MB；15 MB 兼容这类页面，同时挡住异常网页。
MAX_HTML_BYTES = 15 * 1024 * 1024
# 单次请求超时（秒）与重定向上限、瞬时故障重试次数
FETCH_TIMEOUT = 25
FETCH_ATTEMPTS = 3
MAX_REDIRECTS = 5
# 正文最短字数：低于此值判定为「没取到正文」（JS 空壳 / 登录墙 / 付费墙）
MIN_ARTICLE_CHARS = 180
# 语义正文边界（article/main/#js_content）自身可信，门槛可放宽
SEMANTIC_MIN_CHARS = 80
# 表格单元格换行、硬换行的占位符（避免被空白折叠吃掉）
_BR = '\x00BR\x00'
_BR_RE = re.compile(re.escape(_BR))
# 块级内容出现在行内上下文时的分块标记：保住结构，不被空白折叠吞掉换行
_BLK = '\x00BLK\x00'

# 正文根候选：按优先级排列，取「该选择器命中的最大元素」
CONTENT_ROOT_SELECTORS = (
    # 微信公众号
    '#js_content', '.rich_media_content',
    # 通用语义边界
    'article', 'main', '[role="main"]',
    # 文档框架：Docusaurus/MDX、VitePress、GitHub Markdown、MkDocs、GitBook、Sphinx
    '.theme-doc-markdown', '.vp-doc', '.markdown-body', '.md-content',
    '.docs-content', '.doc-content', '.rst-content', '.document',
    'div.body', '.body',
    # 博客 / CMS / 自媒体
    '.post-content', '.entry-content', '.article-content', '.article-body',
    '.content-detail', '.rich-text', '#content', '#article', '.markdown',
)
# 这些选择器本身即「语义正文边界」，命中后按语义路径处理（可放宽字数门槛）
SEMANTIC_ROOT_SELECTORS = frozenset({
    '#js_content', '.rich_media_content', 'article', 'main', '[role="main"]',
    '.markdown-body', '.theme-doc-markdown', '.vp-doc',
})

# 彻底丢弃的标签
_SKIP_TAGS = frozenset({
    'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'iframe',
    'object', 'embed', 'applet', 'form', 'input', 'select', 'textarea', 'button',
    'link', 'meta', 'base', 'head', 'title', 'nav', 'video', 'audio', 'source',
    'track', 'map', 'area', 'dialog', 'marquee', 'blink', 'xmp', 'plaintext',
})
# 块级标签：决定「先累计行内文本还是先断段」
_BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'body', 'center', 'dd',
    'details', 'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure',
    'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup',
    'hr', 'html', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary',
    'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
})
# 版式噪声：仅在这些元素出现在正文根内部时移除（正文根之外本来就不参与）
_CHROME_SELECTORS = (
    'nav', 'aside', 'footer', 'form', 'button', 'iframe', 'noscript',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[aria-hidden="true"]', '[hidden]',
    '.sidebar', '.side-bar', '.navbar', '.site-header', '.masthead',
    '.breadcrumb', '.table-of-contents', '.toc', '.advertisement', '.ad',
    '.ads', '.advert', '.related-posts', '.recommend', '.comment-list',
    '.comments', '.share', '.social-share', '.pagination', '.pager',
)
# 公众号页面尾部的「关注/点赞/往期」等推广块
_PROMO_TEXT_RE = re.compile(
    r'(点击上方|点击下方|关注公众号|关注我们|长按识别|长按二维码|扫码关注|'
    r'扫描二维码|微信扫一扫|分享到朋友圈|预览时标签不可点|继续滑动看下一个|'
    r'往期推荐|往期回顾|商务合作|投稿邮箱|点赞|在看|星标|设为星标)')
_PROMO_CLASS_RE = re.compile(
    r'(qr_code|qrcode|reward|js_ad|advertisement|promo|mp_profile|'
    r'rich_media_area_extra|js_pc_qr_code)', re.I)
# 懒加载图片属性（按优先级）
_LAZY_IMAGE_ATTRS = (
    'data-src', 'data-original', 'data-lazy-src', 'data-lazyload',
    'data-actualsrc', 'data-original-src', 'data-image-src', 'data-echo',
    'data-url', 'data-file', 'file', 'src',
)
_TRACKING_IMAGE_RE = re.compile(
    r'(spacer|blank\.gif|pixel|1x1|transparent\.gif|tracking|beacon)', re.I)
# 正文字数统计时忽略的空白
_WS_RE = re.compile(r'[ \t\u00a0]+')
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_MD_ESC_RE = re.compile(r'([\\`*_\[\]])')
_LEAD_SYNTAX_RE = re.compile(r'(?m)^([ \t]*)([#>+-]|\d+[.)])([ \t])')
_SECTION_NO_RE = re.compile(r'^(\d+(?:\.\d+)*\.)[ \t]*')
_CHARSET_IN_CT_RE = re.compile(r'charset\s*=\s*["\']?([A-Za-z0-9_.:-]+)', re.I)
# 只有 display:none 才算「隐藏」（见 _is_hidden 的说明）
_HIDDEN_STYLE_RE = re.compile(
    r'(?:^|;)\s*display\s*:\s*none(?:\s*!important)?\s*(?:;|$)', re.I)
_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([A-Za-z0-9_.:-]+)', re.I)
_ENCODING_ALIASES = {
    'gb2312': 'gb18030', 'gbk': 'gb18030', 'gb_2312-80': 'gb18030',
    'utf8': 'utf-8', 'utf-8-sig': 'utf-8', 'latin1': 'latin-1',
    'latin-1': 'latin-1', 'iso8859-1': 'latin-1', 'shift-jis': 'shift_jis',
    'big5-hkscs': 'big5hkscs',
}
# 「弱声明」：这些字符集名覆盖的是纯 ASCII/西欧字符，中文站点把它写在响应头或
# meta 里几乎总是误配（requests 在响应头缺 charset 时也会补一个 ISO-8859-1）。
_WEAK_CHARSETS = frozenset({'latin-1', 'iso-8859-1', 'us-ascii', 'ascii', 'windows-1252'})
_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]')


class WebParseError(ValueError):
    """网页解析失败。``reason`` 与 ``LinkItem.REASON_CHOICES`` 的键一致。"""

    def __init__(self, message, reason='unknown'):
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# 取回：URL 校验 / 编码判定 / 限量读取
# ---------------------------------------------------------------------------
def _host_is_blocked(url):
    """挡住本机、链路本地（含云元数据 169.254.169.254）等不该被抓的地址。

    局域网私网段（10/172.16/192.168）默认放行——本机是个人知识库，解析内网
    资料是合理用法；需要更严时设环境变量 ``KB_WEBPARSE_BLOCK_PRIVATE=1``。
    """
    import os
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return '只支持 http/https 链接（当前为 %s）' % (parsed.scheme or '空')
    host = parsed.hostname
    if not host:
        return '链接缺少主机名'
    if parsed.username or parsed.password:
        return '链接里不能带账号密码'
    low = host.lower()
    if low in ('localhost', 'localhost.localdomain', 'ip6-localhost') or low.endswith('.local'):
        return '不允许抓取本机地址'
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise WebParseError('域名解析失败（%s），请检查链接或本机网络。' % host, 'timeout')
    strict = os.environ.get('KB_WEBPARSE_BLOCK_PRIVATE') == '1'
    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            return '不允许抓取本机或链路本地地址（%s）' % raw_ip
        if ip.is_reserved:
            return '不允许抓取保留地址（%s）' % raw_ip
        if strict and ip.is_private:
            return '不允许抓取内网地址（%s）' % raw_ip
    return ''


def _normalize_charset(name):
    if not name:
        return ''
    low = name.strip().strip('"\'').lower()
    return _ENCODING_ALIASES.get(low, low)


def _sniff_meta_charset(raw):
    head = raw[:8192]
    m = _META_CHARSET_RE.search(head)
    if not m:
        return ''
    try:
        return m.group(1).decode('ascii', 'ignore')
    except Exception:
        return ''


def resolve_encoding(raw, content_type=''):
    """定编码。判据只有一条主规则，其余是兜底：

    **能严格解成含中日韩文字的 UTF-8，就按 UTF-8。**

    理由：中文站点把字符集写成 ``latin-1`` / ``ISO-8859-1``（甚至 GBK）却实际
    发 UTF-8 字节，是极常见的服务端误配；照声明解会把整篇变成乱码。反过来，
    真正的 GBK/Big5 字节序列几乎不可能整体构成合法的 UTF-8（更不会解出中日韩
    文字），所以这条判据不会把非 UTF-8 页面误判。

    声明本身仍按 HTTP 语义排序：``Content-Type`` 的 charset → ``<meta charset>``，
    且只有一个"没有信息"的例外——requests 在响应头缺 charset 时补的 ISO-8859-1
    不是声明，只是默认值。
    """
    explicit_ct = ''
    m = _CHARSET_IN_CT_RE.search(content_type or '')
    if m:
        explicit_ct = _normalize_charset(m.group(1))
        if explicit_ct in _WEAK_CHARSETS:
            explicit_ct = ''
    meta = _normalize_charset(_sniff_meta_charset(raw))

    if _utf8_has_cjk(raw):
        return 'utf-8'

    declared = []
    for enc in (explicit_ct, meta):
        if enc and enc not in declared:
            declared.append(enc)
    for enc in declared:
        if _decodes(raw, enc):
            return enc
    probe = _detect_charset(raw)
    if probe and probe not in declared and _decodes(raw, probe):
        return probe
    if declared:
        return declared[0]
    return probe or 'utf-8'


def _utf8_has_cjk(raw):
    """严格按 UTF-8 解出来且含中日韩文字 —— 说明它就是 UTF-8 内容。"""
    try:
        text = raw[:400000].decode('utf-8', 'strict')
    except (UnicodeDecodeError, LookupError):
        return False
    return bool(_CJK_RE.search(text))


def _detect_charset(raw):
    """内容探测。只喂前 64 KB：chardet 全量跑极慢，且前缀足够定中文编码。"""
    sample = raw[:65536]
    try:
        import charset_normalizer  # requests 的依赖，优先用
        best = charset_normalizer.from_bytes(sample).best()
        if best and best.encoding:
            return _normalize_charset(best.encoding)
    except Exception:
        pass
    try:
        import chardet
        guess = chardet.detect(sample) or {}
        return _normalize_charset(guess.get('encoding') or '')
    except Exception:
        return ''


def _decodes(raw, encoding):
    """严格解码试跑：失败说明这个编码名不对，换下一个。"""
    try:
        raw[:200000].decode(encoding, 'strict')
        return True
    except (UnicodeDecodeError, LookupError, TypeError):
        return False


def _decode(raw, encoding):
    try:
        return raw.decode(encoding, 'strict')
    except (UnicodeDecodeError, LookupError):
        return raw.decode(encoding, 'replace') if encoding else raw.decode('utf-8', 'replace')


def _blocks_private(host):
    return host


def fetch_html_bytes(url, label='网页'):
    """取回页面字节流。返回 ``(raw_bytes, final_url, content_type)``。"""
    last_error = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return _fetch_once(url, label)
        except WebParseError:
            raise
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.SSLError) as e:
            last_error = e
            if attempt < FETCH_ATTEMPTS:
                time.sleep(0.5 * attempt)
    text = str(last_error) or ''
    if isinstance(last_error, requests.exceptions.SSLError):
        raise WebParseError('目标站点 TLS 证书链不完整，无法建立可信连接：%s' % text[:120], 'unknown')
    if isinstance(last_error, requests.exceptions.Timeout):
        raise WebParseError('请求超时（%d 秒）——站点响应过慢或被拦截。' % FETCH_TIMEOUT, 'timeout')
    raise WebParseError('网络请求失败（连接被拒绝/重置，可能被反爬拦截）：%s' % text[:120], 'antibot')


def _fetch_once(url, label):
    session = requests.Session()
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        blocked = _host_is_blocked(current)
        if blocked:
            raise WebParseError(blocked, 'unsupported')
        resp = session.get(current, headers=HEADERS, timeout=FETCH_TIMEOUT,
                           stream=True, allow_redirects=False)
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location')
                if not location:
                    raise WebParseError('%s 返回了空的重定向地址。' % label, 'unknown')
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise WebParseError(
                    _http_error_message(resp.status_code, label),
                    _http_error_reason(resp.status_code))
            declared = resp.headers.get('Content-Length')
            if declared and declared.isdigit() and int(declared) > MAX_HTML_BYTES:
                raise WebParseError('%s 体积 %s，超过 %d MB 上限。'
                                    % (label, _human(int(declared)), MAX_HTML_BYTES // 1048576),
                                    'unsupported')
            chunks, total = [], 0
            for chunk in resp.iter_content(65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_HTML_BYTES:
                    raise WebParseError('%s 解压后超过 %d MB 上限，已停止读取。'
                                        % (label, MAX_HTML_BYTES // 1048576), 'unsupported')
                chunks.append(chunk)
            if not chunks:
                raise WebParseError('%s 返回了空内容。' % label, 'js_render')
            return b''.join(chunks), current, resp.headers.get('Content-Type') or ''
        finally:
            resp.close()
    raise WebParseError('重定向次数超过 %d 次，已停止。' % MAX_REDIRECTS, 'antibot')


def _human(nbytes):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if nbytes < 1024 or unit == 'GB':
            return '%.1f %s' % (nbytes, unit)
        nbytes /= 1024.0


def _http_error_message(code, label):
    if code in (401, 403):
        return '%s 返回 %s：站点拒绝访问（反爬/需登录/防盗链）。' % (label, code)
    if code == 429:
        return '%s 返回 429：请求过于频繁，被限流。' % label
    if code == 402:
        return '%s 返回 402：内容在付费墙后。' % label
    if code in (404, 410):
        return '%s 返回 %s：链接已失效或内容被删除。' % (label, code)
    if code >= 500:
        return '%s 返回 %s：站点服务端异常。' % (label, code)
    return '%s 返回状态码 %s，无法解析。' % (label, code)


def _http_error_reason(code):
    if code in (401, 403, 429):
        return 'antibot'
    if code == 402:
        return 'paywall'
    if code in (404, 410):
        return 'invalid'
    return 'unknown'


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------
def escape_md_text(text):
    """转义会被 Markdown 当语法吃掉、或会撞上站内双链的字符。

    方括号必须转义：``render_markdown`` 会把 ``[[标题]]`` 变成站内双链，
    网页正文里随手出现的 ``[[`` 不该被误判成双链。
    """
    if not text:
        return ''
    text = text.replace('\u00a0', ' ')
    text = _CTRL_RE.sub('', text)
    text = _MD_ESC_RE.sub(r'\\\1', text)
    text = _LEAD_SYNTAX_RE.sub(r'\1\\\2\3', text)
    return text


def _fence_for(text, ch='`'):
    fence = ch * 3
    while fence in text:
        fence += ch
    return fence


class _MarkdownWriter:
    """把 BeautifulSoup 树写成 Markdown，保留结构与常见自媒体的写法。"""

    def __init__(self, base_url, block_images=True):
        self.base = base_url or ''
        self.block_images = block_images
        self.stats = {'code_blocks': 0, 'images': 0, 'tables': 0, 'lists': 0,
                      'headings': 0, 'links': 0}

    # ---------------------------------------------------------------- 对外
    def render(self, root):
        blocks = [b for b in self.blocks_of(root) if b and b.strip()]
        blocks = _normalize_headings(blocks)
        return '\n\n'.join(blocks).strip()

    # ---------------------------------------------------------------- 行内
    def inline_children(self, node):
        return ''.join(self.inline(child) for child in node.children)

    def inline(self, node):
        if isinstance(node, Comment):
            return ''
        if isinstance(node, NavigableString):
            return escape_md_text(str(node))
        if not isinstance(node, Tag):
            return ''
        name = node.name.lower()
        if name in _SKIP_TAGS:
            return ''
        if name == 'br':
            return _BR
        if name == 'wbr':
            return ''
        if name == 'img':
            return self.image_md(node)
        if name in ('strong', 'b'):
            return self._wrap(self.inline_children(node), '**')
        if name in ('em', 'i'):
            return self._wrap(self.inline_children(node), '*')
        if name in ('del', 's', 'strike'):
            return self._wrap(self.inline_children(node), '~~')
        if name in ('code', 'kbd', 'samp', 'tt', 'var'):
            return self.code_span(node)
        if name == 'a':
            return self.link_md(node)
        if name in ('sup', 'sub'):
            return self._wrap(self.inline_children(node), '^')
        if name in ('mark', 'u', 'ins', 'span', 'font', 'abbr', 'cite', 'q',
                    'small', 'big', 'label', 'time', 'bdi', 'bdo', 'ruby',
                    'rt', 'rp', 'picture', 'nobr', 'acronym'):
            return self.inline_children(node)
        if name in _BLOCK_TAGS:
            # 行内上下文里撞见块级元素（浏览器会修正的非法嵌套，如 <p><div>）：
            # 用分块标记隔开，交给 flush 切成独立段落 —— 直接拼 '\n' 会被
            # 空白折叠成空格，整篇结构就没了。
            return _BLK.join(self.blocks_of(node))
        return self.inline_children(node)

    def _wrap(self, md, mark):
        if not md or not md.strip():
            return md or ''
        lead = md[:len(md) - len(md.lstrip())]
        trail = md[len(md.rstrip()):]
        core = md.strip()
        if mark == '^':
            return lead + core + trail
        return '%s%s%s%s%s' % (lead, mark, core, mark, trail)

    def code_span(self, node):
        text = node.get_text('')
        if not text:
            return ''
        text = text.replace('\n', ' ').replace('\u00a0', ' ')
        if '`' in text:
            fence = _fence_for(text, '`')
            return fence + ' ' + text + ' ' + fence
        if text.startswith(' ') or text.endswith(' '):
            return '` ' + text + ' `'
        return '`' + text + '`'

    def link_md(self, node):
        href = (node.get('href') or '').strip()
        text = self.inline_children(node).strip()
        if not text:
            text = self.image_md(node) if node.find('img') else ''
        if href.lower().startswith(('javascript:', 'data:')):
            return text
        absolute = self._absolute(href)
        if not absolute:
            return text
        self.stats['links'] += 1
        if any(ch in absolute for ch in ' ()<>"'):
            return '[%s](<%s>)' % (text, absolute.replace('>', '%3E'))
        return '[%s](%s)' % (text, absolute)

    # ---------------------------------------------------------------- 图片
    def image_md(self, node):
        if not self.block_images:
            return ''
        raw = self._pick_image_src(node)
        absolute = self._absolute(raw)
        if not absolute:
            return ''
        if _TRACKING_IMAGE_RE.search(raw or '') or _TRACKING_IMAGE_RE.search(absolute):
            return ''
        try:
            width = int(re.sub(r'\D', '', str(node.get('width') or '0')) or 0)
            height = int(re.sub(r'\D', '', str(node.get('height') or '0')) or 0)
            if 0 < width <= 2 and 0 < height <= 2:
                return ''
        except ValueError:
            pass
        alt = escape_md_text((node.get('alt') or '').strip())
        title = (node.get('title') or '').strip().replace('"', "'")
        suffix = ' "%s"' % title if title else ''
        self.stats['images'] += 1
        return '![%s](%s%s)' % (alt, absolute, suffix)

    def _pick_image_src(self, node):
        for attr in _LAZY_IMAGE_ATTRS:
            value = (node.get(attr) or '').strip()
            if value and not value.lower().startswith('data:'):
                return value
        srcset = (node.get('srcset') or node.get('data-srcset') or '').strip()
        if srcset:
            best, best_weight = '', -1
            for part in srcset.split(','):
                bits = part.strip().split()
                if not bits:
                    continue
                weight = 1
                if len(bits) > 1 and bits[1].endswith('w') and bits[1][:-1].isdigit():
                    weight = int(bits[1][:-1])
                if weight > best_weight:
                    best, best_weight = bits[0], weight
            if best:
                return best
        return ''

    def _absolute(self, raw):
        if not raw:
            return ''
        raw = raw.strip()
        if raw.startswith('//'):
            scheme = urlparse(self.base).scheme or 'https'
            return '%s:%s' % (scheme, raw)
        if raw.startswith('#'):
            return ''
        try:
            return urljoin(self.base, raw)
        except Exception:
            return ''

    # ---------------------------------------------------------------- 块级
    def blocks_of(self, node):
        """把子节点切成块：行内文本聚成段，块级元素各自成块。"""
        out, run = [], []

        def flush():
            if not run:
                return
            joined = ''.join(run)
            del run[:]
            for piece in joined.split(_BLK):
                text = self._collapse(piece).strip()
                if text:
                    out.append(text)

        for child in node.children:
            if isinstance(child, Tag) and child.name.lower() in _BLOCK_TAGS:
                flush()
                out.extend(self.block(child))
            else:
                run.append(self.inline(child))
        flush()
        return out

    def block(self, node):
        name = node.name.lower()
        if name in _SKIP_TAGS:
            return []
        if _is_hidden(node):
            return []
        if name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            return self.heading_blocks(node)
        if name == 'p':
            text = self._collapse(self.inline_children(node)).strip()
            return [text] if text else []
        if name in ('ul', 'ol'):
            block = self.list_block(node, 0)
            return [block] if block else []
        if name == 'pre':
            block = self.pre_block(node)
            return [block] if block else []
        if name == 'blockquote':
            return self.quote_blocks(node)
        if name == 'table':
            return self.table_blocks(node)
        if name == 'hr':
            return ['---']
        if name == 'figcaption':
            text = self._collapse(self.inline_children(node)).strip()
            return ['*%s*' % text] if text else []
        if name == 'dl':
            return self.dl_blocks(node)
        if name in ('div', 'section', 'article', 'main', 'header', 'aside',
                    'footer', 'figure', 'details', 'summary', 'center', 'body',
                    'html', 'fieldset', 'form', 'hgroup', 'blockquote', 'address'):
            return self.blocks_of(node)
        # 兜底：未知容器一律按「混合内容」处理，块级子元素各自成块
        return self.blocks_of(node)

    def _collapse(self, text):
        """折叠 HTML 里的连续空白（与浏览器一致），再把硬换行还原。"""
        text = _WS_RE.sub(' ', text.replace('\r', '').replace('\n', ' '))
        return _BR_RE.sub('  \n', text)

    def heading_blocks(self, node):
        level = int(node.name[1])
        text = self._collapse(self.inline_children(node)).strip()
        text = re.sub(r'[\u00b6\u00a7#\s]+$', '', text).strip()
        if not text:
            # 微信公众号常见 ``<h2><section><img></section></h2>``：标题里只有媒体
            return self.blocks_of(node)
        match = _SECTION_NO_RE.match(text)
        if match:
            text = match.group(1) + ' ' + text[match.end():].lstrip()
        self.stats['headings'] += 1
        return ['%s %s' % ('#' * level, text)]

    def list_block(self, node, depth):
        ordered = node.name.lower() == 'ol'
        try:
            start = int(str(node.get('start') or '1').strip() or '1')
        except ValueError:
            start = 1
        lines, index = [], 0
        for li in node.find_all('li', recursive=False):
            head_parts, nested = [], []
            for child in li.children:
                if isinstance(child, Tag) and child.name.lower() in ('ul', 'ol'):
                    nested.append(child)
                    continue
                if isinstance(child, Tag) and child.name.lower() in _BLOCK_TAGS:
                    piece = ' '.join(self.block(child))
                    if piece.strip():
                        head_parts.append(piece.strip())
                    continue
                piece = self.inline(child)
                if piece:
                    head_parts.append(piece)
            head = self._collapse(''.join(head_parts) if len(head_parts) == 1
                                  else ' '.join(head_parts))
            head = re.sub(r'\s*\n\s*', ' ', head).strip()
            marker = ('%d. ' % (start + index)) if ordered else '- '
            index += 1
            lines.append('%s%s%s' % (' ' * (depth * 4), marker, head))
            for sub in nested:
                lines.append(self.list_block(sub, depth + 1))
        self.stats['lists'] += 1
        return '\n'.join(lines)

    def quote_blocks(self, node):
        inner = '\n\n'.join(b for b in self.blocks_of(node) if b.strip())
        if not inner.strip():
            return []
        quoted = ['> %s' % line if line.strip() else '>' for line in inner.split('\n')]
        return ['\n'.join(quoted)]

    def dl_blocks(self, node):
        lines = []
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name == 'dt':
                text = self._collapse(self.inline_children(child)).strip()
                if text:
                    lines.append(text)
            elif name == 'dd':
                text = self._collapse(self.inline_children(child)).strip()
                if text:
                    lines.append(':   ' + text)
        return ['\n'.join(lines)] if lines else []

    def pre_block(self, node):
        code_node = node.find('code')
        text = _code_text(code_node if code_node is not None else node)
        text = _dedent_code(text)
        if not text.strip():
            return ''
        lang = _detect_language(code_node if code_node is not None else node, node)
        fence = _fence_for(text)
        self.stats['code_blocks'] += 1
        return '%s%s\n%s\n%s' % (fence, lang, text, fence)

    def table_blocks(self, node):
        """返回块列表：表题与表格必须是两个块。

        表题若与表格同块（只隔一个换行），渲染器会把它们当成同一段落，
        整张表退化成竖线文本——实测 python-markdown 的 tables 扩展要求
        表格前有空行。
        """
        rows = node.find_all('tr')
        if len(rows) < 2:
            # 单行表格多为版式表，退化成文本行
            return self.blocks_of(node)
        header, body, aligns = None, [], []
        for index, tr in enumerate(rows):
            cells = [c for c in tr.find_all(['td', 'th'], recursive=False)]
            if not cells:
                continue
            row = []
            for cell in cells:
                span = 1
                try:
                    span = max(1, int(str(cell.get('colspan') or '1').strip() or '1'))
                except ValueError:
                    span = 1
                value = _cell_md(self, cell)
                if index == 0:
                    aligns.append(_align_of(cell))
                row.extend([value] * span)
            has_th = any(c.name.lower() == 'th' for c in cells)
            if index == 0 and (has_th or True):
                header = row
            else:
                body.append(row)
        if header is None:
            header = body.pop(0) if body else []
        cols = max([len(header)] + [len(r) for r in body]) if (header or body) else 0
        if not cols:
            return []
        while len(aligns) < cols:
            aligns.append('')
        aligns = aligns[:cols]

        def line(row):
            padded = list(row[:cols]) + [''] * max(0, cols - len(row))
            return '| ' + ' | '.join(padded) + ' |'

        separator = []
        for align in aligns:
            separator.append({'center': ':--:', 'right': '--:',
                              'left': ':--'}.get(align, '---'))
        lines = [line(header), '| ' + ' | '.join(separator) + ' |']
        lines.extend(line(r) for r in body)
        self.stats['tables'] += 1
        blocks = ['\n'.join(lines)]
        caption = node.find('caption')
        if caption is not None:
            cap = self._collapse(self.inline_children(caption)).strip()
            if cap:
                blocks.insert(0, '**%s**' % cap)
        return blocks


def _cell_md(writer, cell):
    for nested in cell.find_all('table'):
        nested.decompose()
    text = writer._collapse(writer.inline_children(cell)).strip()
    text = re.sub(r'\s*\n\s*', '<br>', text)
    text = text.replace('|', '\\|')
    return text


def _align_of(cell):
    style = (cell.get('style') or '').lower()
    m = re.search(r'text-align\s*:\s*(left|center|right)', style)
    value = m.group(1) if m else (cell.get('align') or '').lower()
    return value if value in ('left', 'center', 'right') else ''


def _code_text(node):
    """取代码文本：<br>/<div>/<p> 等按行分隔，避免高亮插件把行粘成一行。"""
    parts = []

    def walk(el):
        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            if child.name.lower() in ('br', 'div', 'p', 'li', 'tr'):
                parts.append('\n')
                walk(child)
            else:
                walk(child)

    walk(node)
    text = ''.join(parts).replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')
    return text


def _dedent_code(text):
    """去掉因 HTML 缩进带进代码块的整体缩进（保留代码自身相对缩进）。"""
    lines = text.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(l) - len(l.lstrip(' \t')) for l in lines if l.strip()]
    shift = min(indents) if indents else 0
    if shift:
        trimmed = []
        for line in lines:
            if not line.strip():
                trimmed.append('')
            else:
                trimmed.append(line[shift:] if len(line) >= shift else line.lstrip())
        lines = trimmed
    return '\n'.join(lines)


def _detect_language(code_node, pre_node):
    for node in (code_node, pre_node):
        if node is None:
            continue
        for attr in ('data-lang', 'lang', 'data-language'):
            value = (node.get(attr) or '').strip()
            if value:
                return _clean_lang(value)
        classes = ' '.join(node.get('class') or [])
        for pattern in (r'language-([\w+#.\-]+)', r'lang-([\w+#.\-]+)',
                        r'highlight-source-([\w+#.\-]+)', r'brush:\s*([\w+#.\-]+)',
                        r'prettyprint\s+lang-([\w+#.\-]+)'):
            m = re.search(pattern, classes, re.I)
            if m:
                return _clean_lang(m.group(1))
    return ''


def _clean_lang(value):
    value = re.sub(r'[^\w+#.\-]', '', (value or '').strip().lower())
    return value if 1 < len(value) <= 20 else ''


def _is_hidden(node):
    """只认 ``display:none`` —— 其余「看起来隐藏」的写法都是陷阱：

    - ``visibility:hidden``：微信公众号的 ``#js_content`` 初始就是它，等页面脚本
      执行后才恢复。按"隐藏即丢弃"处理会把整篇正文丢掉（实测 5376 字 → 0 字）。
    - ``opacity:0``：入场动画的初始态，内容是真的。
    - ``height:0``：会误伤 ``min-height:0`` / ``max-height:0`` / ``line-height:0``
      这类完全正常的声明。
    """
    attrs = getattr(node, 'attrs', None)
    if not attrs:
        return False
    style = attrs.get('style') or ''
    return bool(_HIDDEN_STYLE_RE.search(style))


def _normalize_headings(blocks):
    """把标题层级对齐到「页面标题为 #、正文小节从 ## 起」。"""
    levels = [len(b) - len(b.lstrip('#')) for b in blocks
              if re.match(r'^#{1,6} ', b)]
    if not levels:
        return blocks
    minimum = min(levels)
    shift = 2 - minimum
    if shift == 0:
        return blocks
    out = []
    for block in blocks:
        m = re.match(r'^(#{1,6}) (.*)$', block)
        if not m:
            out.append(block)
            continue
        level = max(2, min(6, len(m.group(1)) + shift))
        out.append('%s %s' % ('#' * level, m.group(2)))
    return out


def html_to_markdown(html, base_url='', drop_title=None, clean=True):
    """HTML → Markdown，返回 ``(markdown, stats)``。"""
    soup = BeautifulSoup(html, 'lxml') if _lxml_available() else BeautifulSoup(html, 'html.parser')
    return soup_to_markdown(soup, base_url=base_url, drop_title=drop_title, clean=clean)


def _lxml_available():
    try:
        import lxml  # noqa: F401
        return True
    except Exception:
        return False


def soup_to_markdown(soup, base_url='', drop_title=None, clean=True):
    if clean:
        clean_article_soup(soup)
    writer = _MarkdownWriter(base_url)
    markdown = writer.render(soup)
    if drop_title:
        markdown = _drop_leading_title(markdown, drop_title)
    return markdown, writer.stats


def _class_list(node):
    """取 class 列表。decompose() 之后的 Tag 在 bs4 4.15 里 ``attrs`` 是 None，
    而 ``find_all`` 返回的列表是快照，遍历中就可能在访问已销毁节点。"""
    attrs = getattr(node, 'attrs', None)
    if not attrs:
        return []
    classes = attrs.get('class') or []
    if isinstance(classes, str):
        return [classes]
    return [str(c) for c in classes]


def clean_article_soup(soup, aggressive=False):
    """移除版式噪声与推广块。``aggressive`` 用于整页兜底路径。"""
    for selector in _CHROME_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    if aggressive:
        for node in soup.select('header'):
            node.decompose()
    for node in soup.find_all(True):
        classes = ' '.join(_class_list(node))
        if classes and _PROMO_CLASS_RE.search(classes):
            node.decompose()
    for node in soup.find_all(['p', 'section', 'div', 'span', 'a']):
        if node.parent is None:
            continue
        text = node.get_text(' ', strip=True)
        if text and len(text) <= 40 and _PROMO_TEXT_RE.search(text):
            if len(node.find_all(True, recursive=False)) <= 2:
                node.decompose()
    return soup


def _drop_leading_title(markdown, title):
    target = _title_key(title)
    if not target:
        return markdown
    lines = markdown.split('\n')
    for index, line in enumerate(lines[:6]):
        m = re.match(r'^#{1,6} (.*)$', line)
        if m and _title_key(m.group(1)) == target:
            del lines[index]
            while index < len(lines) and not lines[index].strip():
                del lines[index]
            break
    return '\n'.join(lines).strip()


def _title_key(text):
    return re.sub(r'[\s\u3000]+', '', (text or '')).strip().lower()


# ---------------------------------------------------------------------------
# 正文定位：结构完整性检查
# ---------------------------------------------------------------------------
def structure_signature(soup):
    return {
        'code': len(soup.find_all(['pre'])),
        'image': len(soup.find_all('img')),
        'table': len(soup.find_all('table')),
        'item': len(soup.find_all('li')),
    }


def has_all_structures(expected, actual):
    """兜底结果必须覆盖正文容器的代码/图片/表格/列表，否则判为「丢结构」。"""
    for key, count in expected.items():
        if count and actual.get(key, 0) < count:
            return False
    return True


def find_content_root(soup):
    """按优先级挑正文根，返回 ``(节点, 选择器, 是否语义边界)``。"""
    best_any = None
    best_any_len = 0
    for selector in CONTENT_ROOT_SELECTORS:
        candidates = soup.select(selector)
        if not candidates:
            continue
        node = max(candidates, key=lambda n: len(n.get_text(strip=True)))
        length = len(node.get_text(strip=True))
        semantic = selector in SEMANTIC_ROOT_SELECTORS
        threshold = SEMANTIC_MIN_CHARS if semantic else MIN_ARTICLE_CHARS
        if length >= threshold:
            return node, selector, semantic
        if length > best_any_len:
            best_any, best_any_len = node, length
    if best_any is not None and best_any_len >= SEMANTIC_MIN_CHARS:
        return best_any, 'best-effort', False
    return None, '', False


# ---------------------------------------------------------------------------
# 平台适配
# ---------------------------------------------------------------------------
def detect_platform(url, platform='auto'):
    """判定走哪条解析路径。

    域名是可以自己作证的：公众号正文只在 ``#js_content`` 里，B 站图文页面是
    前端渲染的空壳，都必须走专用路径，因此域名判定优先于用户下拉选择。
    """
    host = (urlparse(url).hostname or '').lower()
    if host == 'mp.weixin.qq.com' or host.endswith('.mp.weixin.qq.com'):
        return 'wechat'
    if platform in ('wechat', 'bilibili'):
        return platform
    if host.endswith('bilibili.com') and bilibili_article_id(url):
        return 'bilibili'
    return 'web'


_BILIBILI_ID_RE = re.compile(r'/(?:read/)?cv(\d+)', re.I)


def bilibili_article_id(url):
    m = _BILIBILI_ID_RE.search(urlparse(url).path or '')
    return m.group(1) if m else ''


def parse_bilibili_article(url):
    """B 站图文：页面是前端渲染的 3 KB 空壳，必须走开放接口。"""
    cv_id = bilibili_article_id(url)
    if not cv_id:
        raise WebParseError('B 站链接里没找到图文编号（cv 号）。若这是视频链接，'
                            '请改用「视频转图文」入口。', 'unsupported')
    api = 'https://api.bilibili.com/x/article/view?id=%s' % cv_id
    raw, _final, _ct = fetch_html_bytes(api, 'B站图文接口')
    try:
        import json
        payload = json.loads(raw.decode('utf-8', 'replace'))
    except Exception:
        raise WebParseError('B 站图文接口返回了无法解析的内容。', 'unknown')
    if payload.get('code') != 0:
        code = payload.get('code')
        if code in (-509, -412, -799):
            raise WebParseError('B 站接口触发风控/限流（code=%s），请稍后重试。' % code,
                                'antibot')
        raise WebParseError('B 站图文接口返回失败（code=%s）：图文可能已删除或需要登录。'
                            % code, 'js_render')
    data = payload.get('data') or {}
    content_html = data.get('content') or ''
    if not content_html.strip():
        raise WebParseError('B 站图文接口没有返回正文（可能为纯视频稿）。', 'js_render')
    title = re.sub(r'\s+', ' ', (data.get('title') or '').strip()) or 'B站图文'
    author = ((data.get('author') or {}).get('name') if isinstance(data.get('author'), dict)
              else data.get('author')) or ''
    published = ''
    ts = data.get('publish_time') or data.get('ctime')
    if ts:
        try:
            published = time.strftime('%Y-%m-%d', time.localtime(int(ts)))
        except Exception:
            published = ''
    markdown, stats = html_to_markdown(content_html, base_url='https://www.bilibili.com/',
                                       drop_title=title)
    return {'title': title, 'markdown': markdown, 'author': author,
            'published': published, 'final_url': url, 'platform': 'bilibili',
            'stats': stats}


def read_meta(soup, selectors):
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        value = (node.get('content') if node.has_attr('content')
                 else node.get_text(' ', strip=True))
        if value and value.strip():
            return value.strip()
    return ''


def extract_title(soup, readable_title='', fallback=''):
    title = read_meta(soup, ['meta[property="og:title"]', 'meta[name="twitter:title"]',
                            'meta[name="title"]'])
    if not title:
        title = read_meta(soup, ['#activity-name', 'h1.article-title',
                                 '.article-title', 'h1'])
    if not title:
        title = readable_title
    if not title and soup.title:
        title = soup.title.get_text(' ', strip=True)
    title = re.sub(r'\s+', ' ', (title or '')).strip()
    if not title or title.lower().startswith(('http://', 'https://')):
        title = re.sub(r'\s+', ' ', (fallback or '')).strip()
    return title[:180] or '未命名文章'


def parse_article(url, platform='auto'):
    """解析网页正文。返回 dict：title / markdown / author / published / stats。"""
    url = (url or '').strip()
    if not url:
        raise WebParseError('请输入要解析的链接。', 'unsupported')
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url.lstrip('/')
    kind = detect_platform(url, platform)
    if kind == 'bilibili':
        return parse_bilibili_article(url)

    raw, final_url, content_type = fetch_html_bytes(url)
    encoding = resolve_encoding(raw, content_type)
    html = _decode(raw, encoding)
    soup = BeautifulSoup(html, 'lxml') if _lxml_available() else BeautifulSoup(html, 'html.parser')

    title = extract_title(soup, fallback=final_url)
    author = read_meta(soup, ['meta[name="author"]', 'meta[property="article:author"]',
                              '#js_name', '.author-name'])
    published = read_meta(soup, ['meta[property="article:published_time"]',
                                 'meta[name="publishdate"]', 'meta[name="date"]',
                                 '#publish_time', 'em#publish_time'])

    # 反爬/登录墙的明确信号：宁可归档到链接库，也不入库一堆残渣
    body_text = soup.get_text(' ', strip=True)[:4000]
    wall = _detect_wall(body_text)
    if wall:
        raise WebParseError(wall[0], wall[1])

    full_page = BeautifulSoup(html, 'lxml') if _lxml_available() else BeautifulSoup(html, 'html.parser')
    clean_article_soup(full_page, aggressive=True)
    root, root_selector, semantic = find_content_root(full_page)

    markdown, stats = '', {}
    if root is not None:
        root_copy = BeautifulSoup(str(root), 'lxml') if _lxml_available() else BeautifulSoup(str(root), 'html.parser')
        clean_article_soup(root_copy, aggressive=not semantic)
        writer = _MarkdownWriter(final_url)
        markdown = writer.render(root_copy)
        stats = writer.stats
        expected = structure_signature(root_copy)
    else:
        expected = {}

    readable_markdown, readable_stats = '', {}
    if kind != 'wechat':
        readable_markdown, readable_stats = _readability_markdown(html, final_url)

    chosen = 'content-root' if markdown else ''
    if readable_markdown:
        if not markdown:
            markdown, stats, chosen = readable_markdown, readable_stats, 'readability'
        else:
            # 结构完整性优先：Readability 常把代码/图片/表格删掉
            if root is not None:
                readable_counts = _counts_in_markdown(readable_markdown)
                if has_all_structures(expected, readable_counts) and \
                        len(readable_markdown) > len(markdown) * 1.1:
                    markdown, stats, chosen = readable_markdown, readable_stats, 'readability'
                elif not has_all_structures(expected, _counts_in_markdown(markdown)):
                    chosen = 'content-root'

    text_length = len(re.sub(r'\s', '', markdown))
    threshold = SEMANTIC_MIN_CHARS if semantic else MIN_ARTICLE_CHARS
    if not markdown.strip() or text_length < threshold:
        raise WebParseError(_short_body_message(soup, text_length, final_url), 'js_render')

    markdown = _drop_leading_title(markdown, title)
    header = ['# %s' % title, '', '> 来源：%s' % url]
    if author:
        header.append('> 作者：%s' % author)
    if published:
        header.append('> 发布：%s' % published)
    header.append('> 平台识别：%s' % kind)
    markdown = '\n'.join(header) + '\n\n' + markdown.strip() + '\n'

    return {'title': title, 'markdown': markdown, 'author': author,
            'published': published, 'final_url': final_url, 'platform': kind,
            'root': root_selector, 'strategy': chosen, 'stats': stats,
            'encoding': encoding, 'chars': text_length}


def _readability_markdown(html, base_url):
    try:
        from readability import Document
        readable = Document(html)
        summary = readable.summary() or ''
        readable_title = readable.short_title() or ''
    except Exception:
        return '', {}
    if not summary.strip():
        return '', {}
    soup = BeautifulSoup(summary, 'lxml') if _lxml_available() else BeautifulSoup(summary, 'html.parser')
    clean_article_soup(soup)
    writer = _MarkdownWriter(base_url)
    return writer.render(soup), writer.stats


def _counts_in_markdown(markdown):
    return {
        'code': len(re.findall(r'(?m)^\s*```', markdown)) // 2,
        'image': len(re.findall(r'!\[', markdown)),
        'table': len(re.findall(r'(?m)^\|.*\|\s*$', markdown)) and
                 len(re.findall(r'(?m)^\|\s*:?-{2,}', markdown)),
        'item': len(re.findall(r'(?m)^\s*(?:[-*+]|\d+\.)\s', markdown)),
    }


def _detect_wall(body_text):
    if re.search(r'环境异常|请在微信客户端打开|完成验证后即可继续访问|该内容已被发布者删除', body_text):
        return '页面要求验证或需在微信客户端打开，本机无法取到正文。', 'antibot'
    if re.search(r'登录后(?:方可)?(?:查看|阅读)|请先登录|登录以继续|Sign in to continue', body_text):
        return '内容需要登录后才能查看。', 'login_wall'
    if re.search(r'订阅(?:后)?(?:可)?阅读|开通会员|付费(?:后)?阅读|仅限付费用户', body_text):
        return '内容在付费墙后。', 'paywall'
    return None


def _short_body_message(soup, text_length, final_url=''):
    host = (urlparse(final_url).hostname or '').lower()
    if host.endswith('bilibili.com'):
        return ('B 站链接没取到正文（仅 %d 字）：图文稿请确认是 /read/cv 形式，'
                '视频稿请改用「视频转图文」入口。' % text_length)
    if soup.find('div', id='root') is not None or soup.find('div', id='app') is not None:
        return ('页面正文由前端脚本渲染（SPA），服务端只能拿到 %d 字空壳；'
                '已归档到链接库，可改用浏览器打开后复制正文。' % text_length)
    return ('未能提取到足够正文（仅 %d 字，阈值 %d 字）：可能需登录、有付费墙'
            '或启用了反抓取保护。' % (text_length, MIN_ARTICLE_CHARS))


__all__ = ['WebParseError', 'parse_article', 'html_to_markdown', 'escape_md_text',
           'resolve_encoding', 'find_content_root', 'has_all_structures',
           'structure_signature', 'detect_platform', 'HEADERS']
