from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware




class ASGIRequestAdapter:
    def __init__(self, scope):
        self.META = {}
        self.META['REMOTE_ADDR'] = scope.get("client", [""])[0]
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        self.META['HTTP_USER_AGENT'] = headers.get("user-agent", "")

@database_sync_to_async
def get_user_from_token(token):
    from rest_framework_simplejwt.tokens import AccessToken
    from django.contrib.auth.models import AnonymousUser
    from users.models import ChatUser
    if not token:
        return AnonymousUser()
    try:
        decoded = AccessToken(token)
        user_id = decoded["user_id"]
        return ChatUser.objects.get(id=user_id)
    except Exception:
        return AnonymousUser()

class JWTClientBindingASGIMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        token = parse_qs(query_string).get("token", [None])[0]
        
        scope["user"] = await get_user_from_token(token)
        return await super().__call__(scope, receive, send)
