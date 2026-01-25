import jwt
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from asgiref.sync import async_to_sync

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import ChatUser, Contact
from .pagination import ContactCursorPagination, UserCursorPagination
from .serializers import UserRegistrationSerializer, ContactSerializer, ContactUserSerializer, InitAvatarUploadIn, ConfirmAvatarUploadIn
from utils.response import success_response, error_response
from utils.auth_util import generate_otp, generate_email_token
from utils.jwt_util import issue_token_for_user, verify_token_signature
from background_worker.users.tasks import send_templated_email_task

from .services import AvatarService

from utils.redis_client import ChatRedisService



class RegisterUserView(APIView):
    @swagger_auto_schema(
        request_body=UserRegistrationSerializer,
        responses={
            201: openapi.Response(description="User registered"),
            400: openapi.Response(description="Validation failed")
        },
        operation_description="Register a new user",
        tags=["Authentication"]
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            send_templated_email_task.delay(
                subject="Welcome to Our Platform!",
                to_email=user.email,
                template_name="emails/welcome_email.html",
                context={"user_email": user.email}
            )
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
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, format='email')
            },
            required=['email']
        ),
        responses={
            200: openapi.Response(description="OTP sent"),
            404: openapi.Response(description="User not found")
        },
        operation_description="Send OTP to user's email",
        tags=["Authentication"]
    )
    def post(self, request):
        email = request.data.get('email')
        try:
            user = ChatUser.objects.get(email=email)  
            otp = generate_otp()
            cache.set(f"otp_{email}", otp, timeout=300)  # 5 min

            email_token = generate_email_token(email)

            send_templated_email_task.delay(
                subject="Your OTP Code",
                to_email=email,
                template_name="emails/otp_email.html",
                context={"otp": otp, "user_email": email}
            )

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
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'token': openapi.Schema(type=openapi.TYPE_STRING),
                'otp': openapi.Schema(type=openapi.TYPE_STRING)
            },
            required=['token', 'otp']
        ),
        responses={
            200: openapi.Response(description="OTP verified"),
            400: openapi.Response(description="Invalid OTP or token"),
            401: openapi.Response(description="Token expired")
        },
        operation_description="Verify OTP and issue JWT tokens",
        tags=["Authentication"]
    )
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
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'refresh': openapi.Schema(type=openapi.TYPE_STRING)
            },
            required=['refresh']
        ),
        responses={
            200: openapi.Response(description="Token refreshed"),
            400: openapi.Response(description="Missing or invalid token"),
            401: openapi.Response(description="Token expired or invalid"),
            403: openapi.Response(description="Client mismatch"),
            404: openapi.Response(description="User not found")
        },
        operation_description="Refresh JWT tokens using a valid refresh token",
        tags=["Authentication"]
    )
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
    
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'refresh': openapi.Schema(type=openapi.TYPE_STRING)
            },
            required=['refresh']
        ),
        responses={
            200: openapi.Response(description="Logout successful"),
            400: openapi.Response(description="Missing refresh token"),
            401: openapi.Response(description="Invalid or expired token")
        },
        operation_description="Blacklist refresh token to log out user",
        tags=["Authentication"]
    )
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
            





class AddContactView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        identifier = request.data.get('identifier')

        if not identifier:
            return error_response(
                message="Identifier (email or username) is required",
                errors={"identifier": ["This field is required"]},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            contact_user = ChatUser.objects.get(
                Q(email=identifier) | Q(username=identifier)
            )
        except ChatUser.DoesNotExist:
            return error_response(
                message="User not found",
                errors={"identifier": ["No user matches this identifier"]},
                status=status.HTTP_404_NOT_FOUND
            )

        if contact_user == request.user:
            return error_response(
                message="Cannot add yourself as a contact",
                errors={"identifier": ["Self-addition is not allowed"]},
                status=status.HTTP_400_BAD_REQUEST
            )

        contact, created = Contact.objects.get_or_create(
            owner=request.user,
            contact_user=contact_user
        )

        response_data = {
            "id": contact_user.id,
            "email": contact_user.email,
            "username": contact_user.username,
            "full_name": contact_user.full_name,
            "avatar_url": contact_user.avatar_url
        }

        if not created:
            return success_response(
                message="Already in contacts",
                data=response_data,
                status=status.HTTP_200_OK
            )

        return success_response(
            message="Contact added successfully",
            data=response_data,
            status=status.HTTP_201_CREATED
        )
        


class GetContactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:

            contacts = Contact.objects.filter(owner=request.user)\
                .select_related('contact_user')\
                .order_by('contact_user__full_name')

            paginator = ContactCursorPagination()
            page = paginator.paginate_queryset(contacts, request)
            

            if page:
                contact_user_ids = [c.contact_user_id for c in page]
                
                online_status_map = async_to_sync(ChatRedisService.get_online_status_batch)(contact_user_ids)
            else:
                online_status_map = {}

            serializer = ContactSerializer(
                page, 
                many=True, 
                context={'online_status_map': online_status_map}
            )

            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return error_response(message="Failed to retrieve contacts", errors=str(e), status=500)
        



class ExploreUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            users = ChatUser.objects.exclude(id=request.user.id)

            paginator = UserCursorPagination()
            page = paginator.paginate_queryset(users, request)

            serializer = ContactUserSerializer(page, many=True)

            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            return error_response(message="Failed to retrieve users", errors=str(e), status=500)
        
        
        
        
        

class UserAvatarView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = InitAvatarUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(errors=ser.errors, status=400)
        
        d = ser.validated_data
        
        try:
            result = AvatarService.generate_avatar_upload_url(
                user=request.user,
                file_name=d['file_name'],
                content_type=d['content_type']
            )
            return success_response(
                message="Upload initialized",
                data=result,
                status=200
            )
        except Exception as e:
            return error_response(message="Failed to generate upload URL", status=500)

    def put(self, request):
        ser = ConfirmAvatarUploadIn(data=request.data)
        if not ser.is_valid():
            return error_response(errors=ser.errors, status=400)
        
        temp_key = ser.validated_data['object_key']
        
        try:
            new_url = AvatarService.confirm_avatar_update(request.user, temp_key)
            return success_response(
                message="Avatar updated successfully",
                data={"avatar_url": new_url},
                status=200
            )
        except ValueError as e:
            return error_response(message=str(e), status=403)
        except Exception as e:
            return error_response(message="Confirmation failed. File may be missing or expired.", status=400)