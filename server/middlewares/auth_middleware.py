from rest_framework.response import Response
from rest_framework_simplejwt.tokens import AccessToken
from utils.jwt_util import verify_token_signature
from utils.response import error_response  # ✅ import your error wrapper

class JWTClientBindingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth.split(' ')[1]
            try:
                decoded = AccessToken(token)
                if not verify_token_signature(decoded, request):
                    return error_response(
                        message="Client mismatch",
                        errors={"token": ["Token does not match client signature"]},
                        status=403
                    )
            except Exception:
                return error_response(
                    message="Invalid token",
                    errors={"token": ["Token is invalid or malformed"]},
                    status=401
                )
        return self.get_response(request)
