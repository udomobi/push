from .views import ChannelCRUDL, ChannelLogCRUDL
from django.conf.urls import patterns, url

urlpatterns = ChannelCRUDL().as_urlpatterns()
urlpatterns += ChannelLogCRUDL().as_urlpatterns()
urlpatterns += url(r'^utils/carriers/$', 'temba.channels.utils.get_carriers', name='carriers'),
