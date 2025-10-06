"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

import os
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic.base import RedirectView
from core.admin import core_admin_site

favicon_view = RedirectView.as_view(url="/static/favicon.ico", permanent=True)

if settings.DEMO:
    # from django.contrib import admin
    class AccessUser:
        has_module_perms = has_perm = __getattr__ = lambda s, *a, **kw: True

    core_admin_site.has_permission = lambda r: setattr(r, "user", AccessUser()) or True

urlpatterns = [
    path("favicon.ico", favicon_view),
    # path("log/", log, name="log"),
    path("api/", include("core.api_urls")),
    path("", include("core.frontend_urls")),
    path("rss/", include("core.urls")),
    path("admin/", core_admin_site.urls),
]

if settings.DEBUG:
    urlpatterns.insert(1, path("__debug__/", include("debug_toolbar.urls")))
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
