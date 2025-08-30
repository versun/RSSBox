from django.urls import path

from . import views
from . import digest_views

app_name = "core"
urlpatterns = [
    # 日报相关URL
    path("digest/", digest_views.digest_list, name="digest_list"),
    path("digest/rss/<str:digest_slug>", digest_views.digest_rss, name="digest_rss"),
    path("digest/rss/<str:digest_slug>/", digest_views.digest_rss, name="digest_rss_trailing"),
    path("digest/json/<str:digest_slug>", digest_views.digest_json, name="digest_json"),
    path("digest/json/<str:digest_slug>/", digest_views.digest_json, name="digest_json_trailing"),
    path("digest/status/<str:digest_slug>", digest_views.digest_status, name="digest_status"),
    path("digest/status/<str:digest_slug>/", digest_views.digest_status, name="digest_status_trailing"),
    path("digest/article/<int:article_id>", digest_views.digest_article_detail, name="digest_article_detail"),
    path("digest/article/<int:article_id>/", digest_views.digest_article_detail, name="digest_article_detail_trailing"),
    
    # 原有的URL模式
    # path("filter/<str:name>", views.filter, name="filter"),
    path(
        "tag/proxy/<str:tag>",
        views.tag,
        kwargs={"feed_type": "o", "format": "xml"},
    ),
    path(
        "tag/proxy/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "o", "format": "xml"},
    ),
    path(
        "tag/json/<str:tag>",
        views.tag,
        kwargs={"feed_type": "t", "format": "json"},
    ),
    path(
        "tag/json/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "t", "format": "json"},
    ),
    path(
        "tag/<str:tag>",
        views.tag,
        kwargs={"feed_type": "t", "format": "xml"},
    ),
    path(
        "tag/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "t", "format": "xml"},
    ),
    path(
        "proxy/<str:feed_slug>", views.rss, kwargs={"feed_type": "o", "format": "xml"}
    ),
    path(
        "proxy/<str:feed_slug>/", views.rss, kwargs={"feed_type": "o", "format": "xml"}
    ),
    path(
        "json/<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "format": "json"}
    ),
    path(
        "json/<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "format": "json"}
    ),
    path("import_opml/", views.import_opml, name="import_opml"),
    path("<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
    path("<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
]
