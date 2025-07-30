from django.contrib.admin import AdminSite
from django.urls import path
from django.utils.translation import gettext_lazy as _
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.shortcuts import render, redirect

from utils.modelAdmin_utils import (
    get_all_app_models,
    status_icon,
)
from core.models import Feed, Filter


class CoreAdminSite(AdminSite):
    site_header = _("RSS Translator Admin")
    site_title = _("RSS Translator")
    index_title = _("Dashboard")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("translator/add", translator_add_view, name="translator_add"),
            path("translator/list", translator_list_view, name="translator_list"),
        ]
        return custom_urls + urls

    def get_app_list(self, request, app_label=None):
        # app_list = super().get_app_list(request, app_label)
        app_list = [
            {
                "name": "",
                "app_label": "core",
                "app_url": "/core/",
                "has_module_perms": True,
                "models": [
                    {
                        "model": Feed,
                        "name": "Feeds",
                        "object_name": "Feed",
                        "perms": {
                            "add": True,
                            "change": True,
                            "delete": True,
                            "view": True,
                        },
                        "admin_url": "/core/feed/",
                        "add_url": "/core/feed/add/",
                        "view_only": False,
                    }
                ],
            },
            {
                "name": "",
                "app_label": "engine",
                "models": [
                    {
                        "name": "Translator",
                        "object_name": "Translator",
                        "admin_url": "/translator/list",
                        "add_url": "/translator/add",
                    },
                    {
                        "model": Filter,
                        "name": "Filter",
                        "object_name": "Filter",
                        "perms": {
                            "add": True,
                            "change": True,
                            "delete": True,
                            "view": True,
                        },
                        "admin_url": "/core/filter/",
                        "add_url": "/core/filter/add/",
                        "view_only": False,
                    },
                ],
            },
        ]

        return app_list


class TranslatorPaginator(Paginator):
    def __init__(self):
        super().__init__(self, 100)

        self.translator_count = len(get_all_app_models("translator"))

    @property
    def count(self):
        return self.translator_count

    def page(self, number):
        limit = self.per_page
        offset = (number - 1) * self.per_page
        return self._get_page(
            self.enqueued_items(limit, offset),
            number,
            self,
        )

    # Copied from Huey's SqliteStorage with some modifications to allow pagination
    def enqueued_items(self, limit, offset):
        translators = get_all_app_models("translator")
        translator_list = []
        for model in translators:
            objects = (
                model.objects.all()
                .order_by("name")
                .values_list("id", "name", "valid")[offset : offset + limit]
            )
            for obj_id, obj_name, obj_valid in objects:
                translator_list.append(
                    {
                        "id": obj_id,
                        "table_name": model._meta.db_table.split("_")[1],
                        "name": obj_name,
                        "valid": status_icon(obj_valid),
                        "provider": model._meta.verbose_name,
                    }
                )

        return translator_list


def translator_list_view(request):
    page_number = int(request.GET.get("p", 1))
    paginator = TranslatorPaginator()
    page = paginator.get_page(page_number)
    page_range = paginator.get_elided_page_range(page_number, on_each_side=2, on_ends=2)

    context = {
        **core_admin_site.each_context(request),
        "title": "Translator",
        "page": page,
        "page_range": page_range,
        "translators": page.object_list,
    }
    return render(request, "admin/translator.html", context)


def translator_add_view(request):
    if request.method == "POST":
        translator_name = request.POST.get("translator_name", "/")
        # redirect to example.com/translator/translator_name/add
        target = f"/translator/{translator_name}/add"
        return (
            redirect(target)
            if url_has_allowed_host_and_scheme(target, allowed_hosts=None)
            else redirect("/")
        )
    else:
        models = get_all_app_models("translator")
        translator_list = []
        for model in models:
            translator_list.append(
                {
                    "table_name": model._meta.db_table.split("_")[1],
                    "provider": model._meta.verbose_name,
                }
            )
        context = {
            **core_admin_site.each_context(request),
            "translator_choices": translator_list,
        }
        return render(request, "admin/translator_add.html", context)


core_admin_site = CoreAdminSite()
