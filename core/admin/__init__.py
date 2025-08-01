from django.conf import settings
from django.contrib.auth.models import User, Group
from core.admin.admin_site import core_admin_site, AgentPaginator
from core.admin.agent_admin import *
from core.admin.feed_admin import *
from core.admin.filter_admin import *

if settings.USER_MANAGEMENT:
    core_admin_site.register(User)
    core_admin_site.register(Group)
