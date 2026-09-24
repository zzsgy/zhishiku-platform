from django.contrib import admin
from .models import Advise, Report, WorkRecord

admin.site.register(WorkRecord)
admin.site.register(Report)
admin.site.register(Advise)
