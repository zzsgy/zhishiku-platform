"""通用模板过滤器 / 标签。"""
import os

from django import template

register = template.Library()


@register.simple_tag
def vstatic(path):
    """带内容版本号的静态资源 URL：/static/css/theme.css?v=<文件 mtime>。

    为什么必须这么做：模板里写死 `/static/css/theme.css` 时 URL 恒定不变，
    浏览器会一直命中内存缓存、**根本不再向服务器发请求**，于是样式改完之后
    用户看到的仍是旧文件 —— 实测因此把「批注台已修复」误判成「批注台被删除」，
    排查了半天才发现服务端一切正常、只是浏览器没取新 CSS。
    把 mtime 拼进查询串后，文件一改 URL 就变，浏览器必然重新拉取。
    以后改样式/脚本不必再让用户按 Ctrl+F5。
    """
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static

    url = static(path)
    try:
        found = finders.find(path)
        if found:
            return '%s?v=%d' % (url, int(os.path.getmtime(found)))
    except Exception:
        pass
    return url


@register.filter
def get_item(d, key):
    """字典取值：{{ mydict|get_item:somekey }}"""
    if hasattr(d, 'get'):
        return d.get(key)
    return ''


@register.filter
def attr(obj, name):
    """对象属性取值：{{ obj|attr:'field' }}"""
    try:
        return getattr(obj, name)
    except Exception:
        return ''
