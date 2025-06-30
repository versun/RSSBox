from django.conf import settings


def version(request):
    return {"VERSION": settings.VERSION}
