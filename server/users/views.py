import jwt
from django.conf import settings
from django.core.cache import cache

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .models import ChatUser
from .serializers import UserRegistrationSerializer
from utils.response import success_response, error_response
from utils.auth_util import generate_otp, generate_email_token
from utils.jwt_util import issue_token_for_user, verify_token_signature
from background_worker.users.tasks import send_templated_email_task


class RegisterUserView(APIView):
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # send_templated_email_task.delay(
            #     subject="Welcome to Our Platform!",
            #     to_email=user.email,
            #     template_name="emails/welcome_email.html",
            #     context={"user_email": user.email}
            # )
            return success_response(
                message="User registered",
                data={"id": user.id, "email": user.email},
                status=201
            )
        return error_response(
            message="Validation failed",
            errors=serializer.errors,
            status=400
        )


class SendOTPView(APIView):
    def post(self, request):
        email = request.data.get('email')
        try:
            user = ChatUser.objects.get(email=email)  
            otp = generate_otp()
            cache.set(f"otp_{email}", otp, timeout=300)  # 5 min

            email_token = generate_email_token(email)

            # send_templated_email_task.delay(
            #     subject="Your OTP Code",
            #     to_email=email,
            #     template_name="emails/otp_email.html",
            #     context={"otp": otp, "user_email": email}
            # )

            return success_response(
                message="OTP sent",
                data={"otp": otp, "token": email_token},
                status=200
            )
        except ChatUser.DoesNotExist:
            return error_response(
                message="User not found",
                errors={"email": ["No user found with this email"]},
                status=404
            )


class VerifyOTPView(APIView):
    def post(self, request):
        email_token = request.data.get('token')
        otp = request.data.get('otp')

        try:
            payload = jwt.decode(email_token, settings.SECRET_KEY, algorithms=['HS256'])
            email = payload.get('email')
        except jwt.ExpiredSignatureError:
            return error_response(
                message="Token expired",
                errors={"token": ["Email token has expired"]},
                status=401
            )
        except jwt.InvalidTokenError:
            return error_response(
                message="Invalid token",
                errors={"token": ["Email token is invalid"]},
                status=400
            )

        cached_otp = cache.get(f"otp_{email}")
        if cached_otp != otp:
            return error_response(
                message="Invalid OTP",
                errors={"otp": ["OTP does not match"]},
                status=400
            )

        user = ChatUser.objects.get(email=email)
        refresh = issue_token_for_user(user, request)
        return success_response(
            message="OTP verified",
            data={
                "access": str(refresh.access_token),
                "refresh": str(refresh)
            },
            status=200
        )


class CustomTokenRefreshView(APIView):
    def post(self, request):
        token_str = request.data.get("refresh")
        if not token_str:
            return error_response(
                message="Missing refresh token",
                errors={"refresh": ["This field is required"]},
                status=400
            )

        try:
            refresh = RefreshToken(token_str)
            if not verify_token_signature(refresh, request):
                return error_response(
                    message="Client mismatch",
                    errors={"token": ["Token does not match client signature"]},
                    status=403
                )

            refresh.verify()

            user_id = refresh.get("user_id")
            user = ChatUser.objects.get(id=user_id)
            new_token = issue_token_for_user(user, request)

            return success_response(
                message="Token refreshed",
                data={
                    "access": str(new_token.access_token),
                    "refresh": str(new_token)
                },
                status=200
            )

        except InvalidToken:
            return error_response(
                message="Invalid or expired refresh token",
                errors={"refresh": ["Token is invalid or expired"]},
                status=401
            )
        except TokenError:
            return error_response(
                message="Invalid token",
                errors={"refresh": ["Token verification failed"]},
                status=401
            )
        except ChatUser.DoesNotExist:
            return error_response(
                message="User not found",
                errors={"user": ["No user found for this token"]},
                status=404
            )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return error_response(
                message="Missing refresh token",
                errors={"refresh": ["This field is required"]},
                status=400
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()

            return success_response(
                message="Logout successful",
                data=None,
                status=200
            )

        except (TokenError, InvalidToken):
            return error_response(
                message="Invalid or expired token",
                errors={"refresh": ["Token is invalid"]},
                status=401
            )