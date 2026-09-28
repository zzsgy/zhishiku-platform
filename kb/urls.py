from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static

from dashboard import views as dashboard_views
from starmap import views as starmap_views
from wiki import views as wiki_views
from bookshelf import views as bookshelf_views
from knowledgebase import views as kb_views
from inspiration import views as inspiration_views
from selfmedia import views as selfmedia_views
from office import views as office_views
from collection import views as collection_views
from runarchive import views as runarchive_views
from systemsettings import views as settings_views

urlpatterns = [
    path('', dashboard_views.index, name='home'),
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.svg', permanent=False)),
    path('dashboard/', dashboard_views.index, name='dashboard'),
    path('admin/', admin.site.urls),
    path('starmap/', starmap_views.index, name='starmap'),
    path('wiki/', wiki_views.index, name='wiki'),
    path('wiki/node/<int:pk>/', wiki_views.node_view, name='wiki_node'),
    path('wiki/node/<int:pk>/edit/', wiki_views.node_edit, name='wiki_node_edit'),
    path('wiki/create/', wiki_views.node_create, name='wiki_create'),
    path('wiki/precipitation/', wiki_views.precipitation, name='wiki_precipitation'),
    path('wiki/api/extract/', wiki_views.api_extract, name='wiki_api_extract'),
    path('wiki/precipitation/adopt/<int:pk>/', wiki_views.precipitation_adopt, name='wiki_precip_adopt'),
    path('wiki/precipitation/discard/<int:pk>/', wiki_views.precipitation_discard, name='wiki_precip_discard'),
    path('wiki/node/<int:pk>/grow/', wiki_views.api_grow, name='wiki_node_grow'),
    path('wiki/api/ask/', wiki_views.api_ask, name='wiki_api_ask'),
    path('wiki/api/render/', wiki_views.api_render_md, name='wiki_api_render'),
    path('bookshelf/', bookshelf_views.index, name='bookshelf'),
    path('bookshelf/add/', bookshelf_views.add, name='bookshelf_add'),
    path('bookshelf/golden/', bookshelf_views.golden_list, name='bookshelf_golden'),
    path('bookshelf/book/<int:pk>/', bookshelf_views.book_view, name='bookshelf_book'),
    path('bookshelf/book/<int:pk>/retry/', bookshelf_views.book_retry, name='bookshelf_retry'),
    path('bookshelf/book/<int:pk>/progress/', bookshelf_views.update_progress, name='bookshelf_progress'),
    path('bookshelf/book/<int:pk>/note/', bookshelf_views.add_note, name='bookshelf_note'),
    path('bookshelf/book/<int:pk>/delete/', bookshelf_views.book_delete, name='bookshelf_book_delete'),
    path('bookshelf/api/golden/', bookshelf_views.api_golden, name='bookshelf_api_golden'),
    path('bookshelf/api/ask/', bookshelf_views.api_ask, name='bookshelf_api_ask'),
    path('bookshelf/api/golden_delete/', bookshelf_views.api_golden_delete, name='bookshelf_api_golden_delete'),
    path('bookshelf/api/note/', bookshelf_views.api_note, name='bookshelf_api_note'),
    path('bookshelf/api/note_delete/', bookshelf_views.api_note_delete, name='bookshelf_api_note_delete'),
    path('knowledgebase/', kb_views.index, name='knowledgebase'),
    path('knowledgebase/node/<int:pk>/', kb_views.node_view, name='kb_node'),
    path('knowledgebase/node/<int:pk>/note/', kb_views.add_note, name='kb_node_note'),
    path('knowledgebase/api/note/', kb_views.api_note, name='kb_api_note'),
    path('knowledgebase/api/note_delete/', kb_views.api_note_delete, name='kb_api_note_delete'),
    path('knowledgebase/node/<int:pk>/delete/', kb_views.node_delete, name='kb_node_delete'),
    path('knowledgebase/golden/', kb_views.golden, name='kb_golden'),
    path('knowledgebase/links/', kb_views.link_library, name='kb_links'),
    path('knowledgebase/links/<int:pk>/status/', kb_views.link_status, name='kb_link_status'),
    path('knowledgebase/links/<int:pk>/delete/', kb_views.link_delete, name='kb_link_delete'),
    path('knowledgebase/links/<int:pk>/retry/', kb_views.link_retry, name='kb_link_retry'),
    path('notes/', inspiration_views.notes_page, name='notes'),
    path('notes/api/log/', inspiration_views.api_log, name='notes_api_log'),
    path('inspiration/', inspiration_views.index, name='inspiration'),
    path('inspiration/create/', inspiration_views.create, name='inspiration_create'),
    path('inspiration/api/add/', inspiration_views.api_add, name='inspiration_api_add'),
    path('inspiration/delete/<int:pk>/', inspiration_views.delete_item, name='inspiration_delete'),
    path('selfmedia/', selfmedia_views.index, name='selfmedia'),
    path('selfmedia/create/', selfmedia_views.create, name='selfmedia_create'),
    path('selfmedia/edit/<int:pk>/', selfmedia_views.edit, name='selfmedia_edit'),
    path('selfmedia/delete/<int:pk>/', selfmedia_views.delete, name='selfmedia_delete'),
    path('selfmedia/ai_polish/<int:pk>/', selfmedia_views.ai_polish, name='selfmedia_ai_polish'),
    path('office/', office_views.index, name='office'),
    path('office/work_add/', office_views.work_add, name='office_work_add'),
    path('office/search/', office_views.search, name='office_search'),
    path('office/api/search/', office_views.api_search, name='office_api_search'),
    path('office/api/record_update/', office_views.api_record_update, name='office_api_record_update'),
    path('office/api/record_delete/', office_views.api_record_delete, name='office_api_record_delete'),
    path('office/api/archive_day/', office_views.api_archive_day, name='office_api_archive_day'),
    path('office/advise/', office_views.advise, name='office_advise'),
    path('office/advise/<int:pk>/', office_views.advise_view, name='office_advise_view'),
    path('office/report_gen/', office_views.report_gen, name='office_report_gen'),
    path('office/report/<int:pk>/', office_views.report_view, name='office_report'),
    path('office/workfile/<int:pk>/', office_views.workfile_view, name='office_workfile'),
    path('office/workfile_open/<int:pk>/', office_views.workfile_open, name='office_workfile_open'),
    path('collection/', collection_views.index, name='collection'),
    path('collection/api/recent/', collection_views.recent_items, name='collection_recent'),
    path('collection/input_text/', collection_views.input_text, name='collection_input_text'),
    path('collection/import_local/', collection_views.import_local, name='collection_import_local'),
    path('collection/import_office/', collection_views.import_office, name='collection_import_office'),
    path('collection/parse_web/', collection_views.parse_web, name='collection_parse_web'),
    path('collection/import_image/', collection_views.import_image, name='collection_import_image'),
    path('collection/parse_video/', collection_views.parse_video, name='collection_parse_video'),
    path('collection/link_action/', collection_views.collection_link_action, name='collection_link_action'),
    path('runarchive/', runarchive_views.index, name='runarchive'),
    path('runarchive/export/csv/', runarchive_views.export_csv, name='runarchive_export_csv'),
    path('runarchive/export/md/', runarchive_views.export_md, name='runarchive_export_md'),
    path('settings/', settings_views.index, name='settings'),
    path('settings/save/', settings_views.save_settings, name='settings_save'),
    path('api/providers/', settings_views.api_providers, name='api_providers'),
    path('settings/ai/save/', settings_views.ai_save, name='ai_save'),
    path('settings/ai/delete/<str:pid>/', settings_views.ai_delete, name='ai_delete'),
    path('settings/ai/default/<str:pid>/', settings_views.ai_default, name='ai_default'),
    path('settings/ai/test/<str:pid>/', settings_views.ai_test, name='ai_test'),
    path('settings/ai/ask_all/', settings_views.ai_ask_all, name='ai_ask_all'),
    path('settings/gitee_repos/', settings_views.gitee_repos_api, name='gitee_repos_api'),
    # 备份与迁移（知识备份 / 系统备份 / 迁移备份）
    path('settings/backup/preview/', settings_views.backup_preview, name='backup_preview'),
    path('settings/backup/start/', settings_views.backup_start, name='backup_start'),
    path('settings/backup/list/', settings_views.backup_list, name='backup_list'),
    path('settings/backup/reveal/', settings_views.backup_reveal, name='backup_reveal'),
    path('settings/backup/status/<int:pk>/', settings_views.backup_status, name='backup_status'),
    path('settings/backup/download/<int:pk>/', settings_views.backup_download, name='backup_download'),
    path('settings/backup/delete/<int:pk>/', settings_views.backup_delete, name='backup_delete'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
