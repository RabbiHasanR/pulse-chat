import jwt
from django.conf import settings
from django.core.cache import cache

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken


from .models import ChatUser
from .serializers import UserRegistrationSerializer
from utils.auth_util import generate_otp, generate_email_token
from utils.jwt_util import issue_token_for_user, verify_token_signature



class RegisterUserView(APIView):
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "User registered"}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class SendOTPView(APIView):
    def post(self, request):
        email = request.data.get('email')
        try:
            user = ChatUser.objects.get(email=email) # noqa
            otp = generate_otp()
            cache.set(f"otp_{email}", otp, timeout=300)  # 5 min

            email_token = generate_email_token(email)

            # send_otp_email(email, otp)  # Could be Celery task

            return Response({
                "message": "OTP sent",
                "token": email_token
            }, status=200)
        except ChatUser.DoesNotExist:
            return Response({"error": "User not found"}, status=404)



class VerifyOTPView(APIView):
    def post(self, request):
        email_token = request.data.get('token')
        otp = request.data.get('otp')

        try:
            payload = jwt.decode(email_token, settings.SECRET_KEY, algorithms=['HS256'])
            email = payload.get('email')
        except jwt.ExpiredSignatureError:
            return Response({"error": "Token expired"}, status=401)
        except jwt.InvalidTokenError:
            return Response({"error": "Invalid token"}, status=400)

        cached_otp = cache.get(f"otp_{email}")
        if cached_otp != otp:
            return Response({"error": "Invalid OTP"}, status=400)

        user = ChatUser.objects.get(email=email)
        refresh = issue_token_for_user(user, request)
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh)
        }, status=200)
        


class CustomTokenRefreshView(APIView):
    def post(self, request):
        token_str = request.data.get("refresh")
        if not token_str:
            return Response({"error": "Missing refresh token"}, status=400)

        try:
            refresh = RefreshToken(token_str)

            if not verify_token_signature(refresh, request):
                return Response({"detail": "Client mismatch"}, status=403)
            
            refresh.verify()
            
            jti = refresh.get("jti")
            if BlacklistedToken.objects.filter(token__jti=jti).exists():
                return Response({"error": "Token is blacklisted"}, status=status.HTTP_401_UNAUTHORIZED)

            user_id = refresh.get("user_id")
            user = ChatUser.objects.get(id=user_id)
            new_token = issue_token_for_user(user, request)

            return Response({
                "access": str(new_token.access_token),
                "refresh": str(new_token),
            }, status=200)

        except InvalidToken:
            return Response({"error": "Invalid or expired refresh token"}, status=401)
        except TokenError as e: # noqa
            return Response({"error": "Invalid token"}, status=401)
        except ChatUser.DoesNotExist:
            return Response({"error": "User not found"}, status=404)