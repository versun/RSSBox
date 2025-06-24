from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    # path("filter/<str:name>", views.filter, name="filter"),
    path("category/proxy/<str:category>", views.category, kwargs={"feed_type": "o", "formate":"xml"}),
    path("category/proxy/<str:category>/", views.category, kwargs={"feed_type": "o", "formate":"xml"}),
    path("category/rss/<str:category>", views.category, kwargs={"feed_type": "t", "formate":"xml"}),
    path("category/rss/<str:category>/", views.category, kwargs={"feed_type": "t", "formate":"xml"}),
    path("category/json/<str:category>", views.category, kwargs={"feed_type": "t", "formate":"json"}),
    path("category/json/<str:category>/", views.category, kwargs={"feed_type": "t", "formate":"json"}),
    path("proxy/<str:feed_slug>", views.rss, kwargs={"feed_type": "o", "formate":"xml"}),
    path("proxy/<str:feed_slug>/", views.rss, kwargs={"feed_type": "o", "formate":"xml"}),
    path("rss/<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "formate":"xml"}),
    path("rss/<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "formate":"xml"}),
    path("json/<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "formate":"json"}),
    path("json/<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "formate":"json"}),
    path('import_opml/', views.import_opml, name='import_opml'),

]
