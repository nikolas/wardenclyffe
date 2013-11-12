from django.conf.urls.defaults import patterns, include
from django.contrib import admin
from django.conf import settings
from wardenclyffe.main.feeds import CollectionFeed
import wardenclyffe.main.views as views
from django.views.generic import TemplateView
admin.autodiscover()

urlpatterns = patterns(
    '',
    ('^$', views.IndexView.as_view()),
    ('^dashboard/', views.DashboardView.as_view()),
    ('^recent_operations/', 'wardenclyffe.main.views.recent_operations'),
    ('^slow_operations/', views.SlowOperationsView.as_view()),
    ('^most_recent_operation/',
     'wardenclyffe.main.views.most_recent_operation'),
    ('^accounts/', include('djangowind.urls')),
    ('^cuit/', include('wardenclyffe.cuit.urls')),
    (r'^admin/', include(admin.site.urls)),
    (r'^capture/file_upload', 'wardenclyffe.main.views.test_upload'),
    (r'^add_collection/$', 'wardenclyffe.main.views.add_collection'),
    (r'^collection/(?P<id>\d+)/$', 'wardenclyffe.main.views.collection'),
    (r'^collection/(?P<id>\d+)/videos/$',
     'wardenclyffe.main.views.all_collection_videos'),
    (r'^collection/(?P<id>\d+)/workflows/$',
     'wardenclyffe.main.views.edit_collection_workflows'),
    (r'^collection/(?P<id>\d+)/operations/$',
     'wardenclyffe.main.views.all_collection_operations'),
    (r'^collection/(?P<id>\d+)/edit/$',
     'wardenclyffe.main.views.edit_collection'),
    (r'^collection/(?P<id>\d+)/toggle_active/$',
     'wardenclyffe.main.views.collection_toggle_active'),
    (r'^collection/(?P<id>\d+)/delete/$',
     'wardenclyffe.main.views.delete_collection'),
    (r'^collection/(?P<id>\d+)/remove_tag/(?P<tagname>\w+)/$',
     'wardenclyffe.main.views.remove_tag_from_collection'),
    (r'^collection/(?P<id>\d+)/rss/$', CollectionFeed()),
    (r'^video/(?P<id>\d+)/edit/$', 'wardenclyffe.main.views.edit_video'),
    (r'^video/(?P<id>\d+)/delete/$', 'wardenclyffe.main.views.delete_video'),
    (r'^video/(?P<id>\d+)/remove_tag/(?P<tagname>\w+)/$',
     'wardenclyffe.main.views.remove_tag_from_video'),

    (r'^server/$', views.ServersListView.as_view()),
    (r'^server/add/$', 'wardenclyffe.main.views.add_server'),
    (r'^server/(?P<pk>\d+)/$', views.ServerView.as_view()),
    (r'^server/(?P<id>\d+)/edit/$', 'wardenclyffe.main.views.edit_server'),
    (r'^server/(?P<id>\d+)/delete/$', 'wardenclyffe.main.views.delete_server'),

    (r'^file/$', 'wardenclyffe.main.views.file_index'),
    (r'^file/(?P<id>\d+)/$', 'wardenclyffe.main.views.file'),

    (r'^file/filter/$', 'wardenclyffe.main.views.file_filter'),
    ((r'^operation/(?P<uuid>[a-z0-9]{8}-[a-z0-9]{4}-[a-z0-9]{4}-'
      r'[a-z0-9]{4}-[a-z0-9]{12})/info/$'),
     'wardenclyffe.main.views.operation_info'),
    ((r'^operation/(?P<uuid>[a-z0-9]{8}-[a-z0-9]{4}-[a-z0-9]{4}-'
      r'[a-z0-9]{4}-[a-z0-9]{12})/$'),
     'wardenclyffe.main.views.operation'),

    (r'^bulk_file_operation/$', 'wardenclyffe.main.views.bulk_file_operation'),
    (r'^user/(?P<username>\w+)/', 'wardenclyffe.main.views.user'),
    (r'^file/(?P<id>\d+)/delete/$', 'wardenclyffe.main.views.delete_file'),
    (r'^file/(?P<id>\d+)/surelink/$', 'wardenclyffe.main.views.file_surelink'),
    (r'^file/(?P<id>\d+)/submit_to_workflow/$',
     'wardenclyffe.main.views.file_pcp_submit'),
    (r'^operation/(?P<id>\d+)/delete/$',
     'wardenclyffe.main.views.delete_operation'),
    (r'^operation/(?P<operation_id>\d+)/rerun/$',
     'wardenclyffe.main.views.rerun_operation'),
    (r'^tag/$', views.TagsListView.as_view()),
    (r'^tag/(?P<tagname>\w+)/$', 'wardenclyffe.main.views.tag'),
    (r'^upload/$', 'wardenclyffe.main.views.upload_form'),
    (r'^upload/post/$', 'wardenclyffe.main.views.upload'),
    (r'^scan_directory/$', 'wardenclyffe.main.views.scan_directory'),
    (r'^mediathread/$', 'wardenclyffe.mediathread.views.mediathread'),
    (r'^mediathread/post/$',
     'wardenclyffe.mediathread.views.mediathread_post'),
    (r'^youtube/$', 'wardenclyffe.youtube.views.youtube'),
    (r'^youtube/post/$', 'wardenclyffe.youtube.views.youtube_post'),
    (r'^youtube/done/$', 'wardenclyffe.youtube.views.youtube_done'),
    (r'^uploadify/$', 'wardenclyffe.main.views.uploadify'),
    (r'^done/$', 'wardenclyffe.main.views.done'),
    (r'^posterdone/$', 'wardenclyffe.main.views.posterdone'),
    (r'^received/$', views.ReceivedView.as_view()),
    (r'^surelink/$', 'wardenclyffe.main.views.surelink'),
    (r'^video/$', 'wardenclyffe.main.views.video_index'),
    (r'^video/(?P<id>\d+)/$', 'wardenclyffe.main.views.video'),
    (r'^video/(?P<id>\d+)/pcp_submit/$',
     'wardenclyffe.main.views.video_pcp_submit'),
    (r'^video/(?P<id>\d+)/mediathread_submit/$',
     'wardenclyffe.mediathread.views.video_mediathread_submit'),
    (r'^video/(?P<id>\d+)/add_file/$',
     'wardenclyffe.main.views.video_add_file'),
    (r'^video/(?P<id>\d+)/select_poster/(?P<image_id>\d+)/$',
     'wardenclyffe.main.views.video_select_poster'),
    (r'^list_workflows/$', 'wardenclyffe.main.views.list_workflows'),
    (r'^search/$', 'wardenclyffe.main.views.search'),
    (r'^uuid_search/$', 'wardenclyffe.main.views.uuid_search'),
    (r'^api/tagautocomplete/$', 'wardenclyffe.main.views.tag_autocomplete'),
    (r'^api/subjectautocomplete/$',
     'wardenclyffe.main.views.subject_autocomplete'),
    (r'^celery/', include('djcelery.urls')),
    (r'munin/total_videos/', 'wardenclyffe.main.views.total_videos'),
    (r'munin/total_files/', 'wardenclyffe.main.views.total_files'),
    (r'munin/total_operations/', 'wardenclyffe.main.views.total_operations'),
    (r'munin/total_minutes/', 'wardenclyffe.main.views.total_minutes'),
    ('^munin/', include('munin.urls')),
    ('smoketest/', include('smoketest.urls')),
    (r'^stats/$', TemplateView.as_view(template_name="main/stats.html")),
    (r'^stats/auth/$',
     TemplateView.as_view(template_name="main/auth_stats.html")),
    (r'^uploads/(?P<path>.*)$',
     'django.views.static.serve',
     {'document_root': settings.MEDIA_ROOT}),
)
