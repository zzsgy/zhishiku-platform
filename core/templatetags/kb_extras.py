"""通用模板过滤器。"""
from django import template

register = template.Library()


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
