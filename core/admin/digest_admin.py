from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django import forms
from django.contrib import messages
from core.models.digest import Digest
from core.models.agent import OpenAIAgent
from core.admin.admin_site import core_admin_site
from utils.modelAdmin_utils import status_icon


class PublishDaysWidget(forms.MultipleChoiceField):
    """Custom widget for selecting publish days using checkboxes."""

    def __init__(self, *args, **kwargs):
        choices = [
            ("monday", _("Monday")),
            ("tuesday", _("Tuesday")),
            ("wednesday", _("Wednesday")),
            ("thursday", _("Thursday")),
            ("friday", _("Friday")),
            ("saturday", _("Saturday")),
            ("sunday", _("Sunday")),
        ]
        kwargs["choices"] = choices
        kwargs["widget"] = forms.CheckboxSelectMultiple
        kwargs["required"] = False
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        """Convert JSON list to list of selected values."""
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            # Handle legacy comma-separated string format
            return [day.strip().lower() for day in value.split(",") if day.strip()]
        return value

    def clean(self, value):
        """Convert list of selected values back to JSON list."""
        if not value:
            return []
        # Return as sorted list
        return sorted(value)

    def has_changed(self, initial, data):
        """Override has_changed to handle list comparison properly."""
        if initial is None:
            initial = []
        elif isinstance(initial, str):
            # Handle legacy comma-separated string format
            initial = self.prepare_value(initial)

        if data is None:
            data = []

        # Ensure both are lists for comparison
        if not isinstance(initial, list):
            initial = list(initial) if initial else []
        if not isinstance(data, list):
            data = list(data) if data else []

        # Convert to sets for comparison (order doesn't matter)
        initial_set = set(str(x) for x in initial)
        data_set = set(str(x) for x in data)

        return initial_set != data_set


class DigestAdminForm(forms.ModelForm):
    """Custom form for Digest admin to restrict summarizer choices."""

    publish_days = PublishDaysWidget(
        label=_("Publish Days"),
        help_text=_("Select which days of the week this digest should be published"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit summarizer choices to only valid OpenAI agents
        if "summarizer" in self.fields:
            self.fields["summarizer"].queryset = OpenAIAgent.objects.filter(valid=True)
            self.fields["summarizer"].empty_label = _("Select a valid OpenAI agent...")

    class Meta:
        model = Digest
        fields = "__all__"


@admin.register(Digest, site=core_admin_site)
class DigestAdmin(admin.ModelAdmin):
    """
    Admin interface for Digest model.

    Provides comprehensive management of AI digest configurations including
    tag selection, AI agent assignment, and prompt customization.
    """

    change_form_template = "admin/change_form_with_tabs.html"
    form = DigestAdminForm
    autocomplete_fields = ["tags"]

    list_display = [
        "name",
        "show_url",
        "generation_status",
        "publish_days_display",
        "show_tags",
        "last_generated",
    ]

    list_filter = [
        "is_active",
        "status",
        "days_range",
        "created_at",
        "last_generated",
    ]

    search_fields = [
        "name",
        "slug",
        "description",
        "tags__name",
    ]

    filter_horizontal = [
        "tags",
    ]

    fieldsets = (
        (
            _("Basic Information"),
            {
                "fields": (
                    "name",
                    "slug",
                    "description",
                    "is_active",
                    "show_log",
                )
            },
        ),
        (
            _("Content Configuration"),
            {
                "fields": (
                    "tags",
                    "target_language",
                    "days_range",
                )
            },
        ),
        (
            _("Publishing Schedule"),
            {
                "fields": ("publish_days",),
            },
        ),
        (
            _("Agent Configuration"),
            {
                "fields": (
                    "summarizer",
                    "prompt",
                ),
            },
        ),
        (
            _("Status"),
            {
                "fields": (
                    "status",
                    "last_generated",
                    "total_tokens",
                ),
            },
        ),
    )

    readonly_fields = [
        "status",
        "last_generated",
        "created_at",
        "updated_at",
        "total_tokens",
        "show_log",
    ]

    actions = ["generate_digest_action"]

    @admin.display(description=_("Status"))
    def generation_status(self, obj):
        """Display generation status with visual indicator."""
        if not obj.is_active:
            return "⏸️"
        return status_icon(obj.status)

    generation_status.admin_order_field = "status"

    @admin.display(description=_("Log"))
    def show_log(self, obj):
        return format_html(
            """
            <details>
                <summary>show</summary>
                <div style="max-height: 200px; overflow: auto;">
                    {0}
                </div>
            </details>
            """,
            mark_safe(obj.log),
        )

    @admin.display(description=_("URL"))
    def show_url(self, obj):
        """Display URL with link."""
        return format_html(
            '<a href="/rss/digest/{}">rss</a> | <a href="/rss/digest/json/{}">json</a>',
            obj.slug,
            obj.slug,
        )

    @admin.display(description=_("Tags"))
    def tag_list(self, obj):
        """Display associated tags as a comma-separated list."""
        tags = obj.tags.all()[:3]  # Show first 3 tags
        tag_names = [tag.name for tag in tags]
        if obj.tags.count() > 3:
            tag_names.append(f"... (+{obj.tags.count() - 3} more)")
        return ", ".join(tag_names) if tag_names else "-"

    @admin.display(description=_("tags"))
    def show_tags(self, obj):
        if not obj.tags.exists():  # obj.tags 返回一个QuerySet对象，bool(obj.tags) 总是True，因为QuerySet对象总是被认为是True
            return "-"
        tags_html = "<br>".join(
            f"<a href='{reverse('admin:core_tag_change', args=[t.id])}'>#{t.name}</a>"
            for t in obj.tags.all()
        )
        return format_html(tags_html)

    @admin.display(description=_("AI Agent"))
    def summarizer_name(self, obj):
        """Display summarizer agent name with link."""
        if obj.summarizer:
            url = reverse(
                f"admin:core_{obj.summarizer._meta.model_name}_change",
                args=[obj.summarizer.pk],
            )
            return format_html('<a href="{}">{}</a>', url, obj.summarizer.name)
        return _("No agent assigned")

    @admin.display(description=_("Publish Days"))
    def publish_days_display(self, obj):
        """Display publish days as abbreviated weekday names."""
        days = obj.get_publish_days_list()
        if not days:
            return _("No days selected")

        # Abbreviate day names
        day_abbrevs = {
            "Monday": "Mon",
            "Tuesday": "Tue",
            "Wednesday": "Wed",
            "Thursday": "Thu",
            "Friday": "Fri",
            "Saturday": "Sat",
            "Sunday": "Sun",
        }

        abbrev_days = [day_abbrevs.get(day, day) for day in days]
        return ", ".join(abbrev_days)

    @admin.display(description=_("Generate selected Digests"))
    def generate_digest_action(self, request, queryset):
        """Generate digests for selected items."""
        from core.tasks.generate_digests import DigestGenerator
        from core.tasks.task_manager import task_manager
        import time

        # Only process active digests
        active_digests = queryset.filter(is_active=True)

        if not active_digests:
            self.message_user(
                request,
                _("No active digests selected. Only active digests can be generated."),
                level=messages.WARNING,
            )
            return

        success_count = 0
        error_count = 0

        for digest in active_digests:
            try:
                # Generate unique task name
                task_name = f"digest_generation_{digest.id}_{int(time.time())}"
                digest_generator = DigestGenerator(digest)
                # Submit task to background execution
                digest.status = None
                digest.save()
                future = task_manager.submit_task(task_name, digest_generator.generate)

                success_count += 1

            except Exception as e:
                error_count += 1
                self.message_user(
                    request,
                    _("Failed to generate digest '{}': {}").format(digest.name, str(e)),
                    level=messages.ERROR,
                )

        if success_count > 0:
            self.message_user(
                request,
                _("Successfully started generation for {} digest(s).").format(
                    success_count
                ),
                level=messages.SUCCESS,
            )

        if error_count > 0:
            self.message_user(
                request,
                _("Failed to generate {} digest(s).").format(error_count),
                level=messages.ERROR,
            )

    def get_queryset(self, request):
        """Optimize queryset with prefetch_related for better performance."""
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                "tags",
                "summarizer",
            )
        )

    def save_model(self, request, obj, form, change):
        """Custom save logic if needed."""
        super().save_model(request, obj, form, change)

        if not change:  # New object
            # Generate digest immediately
            from core.tasks.generate_digests import DigestGenerator

            digest_generator = DigestGenerator(obj)
            digest_generator.generate()
            self.message_user(
                request,
                _("Digest '{}' created successfully. Generated immediately.").format(
                    obj.name
                ),
                level="success",
            )
