from django.core.paginator import Paginator


def paginate(request, queryset, per_page=50, parameter='page'):
    page = Paginator(queryset, per_page).get_page(request.GET.get(parameter))
    query = request.GET.copy()
    query.pop(parameter, None)
    page.query_prefix = query.urlencode()
    page.parameter = parameter
    return page
