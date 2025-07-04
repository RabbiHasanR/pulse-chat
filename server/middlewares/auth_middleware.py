from django.http import JsonResponse
from rest_framework_simplejwt.tokens import AccessToken
from utils.jwt_util import get_client_signature

class JWTClientBindingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth.split(' ')[1]
            try:
                decoded = AccessToken(token)
                if get_client_signature(decoded, request):
                    return JsonResponse({"detail": "Client mismatch"}, status=403)
            except Exception:
                return JsonResponse({"detail": "Invalid token"}, status=401)
        return self.get_response(request)
