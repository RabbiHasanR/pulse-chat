import os
import django

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

django.setup()

from channel.routing import websocket_urlpatterns 
from middlewares.websocket_middleware import JWTClientBindingASGIMiddleware 

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": JWTClientBindingASGIMiddleware(
        URLRouter(websocket_urlpatterns)
    ),
})
