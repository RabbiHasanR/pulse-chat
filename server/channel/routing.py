from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/chat/user/(?P<user_id>\d+)/$', consumers.UserSocketConsumer.as_asgi()),
]