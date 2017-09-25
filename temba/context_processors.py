from django.conf import settings


def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(brand=request.branding, websocket_address=settings.WEBSOCKET_ADDRESS, hostname=settings.HOSTNAME,
                support_channel=settings.SUPPORT_CHANNEL)
