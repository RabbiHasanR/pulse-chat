from django.urls import path

from .views import (
    RegisterUserView,
    SendOTPView,
    VerifyOTPView,
    CustomTokenRefreshView
)

urlpatterns = [
    path("register/", RegisterUserView.as_view(), name="register"),

    path("send-otp/", SendOTPView.as_view(), name="send-otp"),

    path("verify-otp/", VerifyOTPView.as_view(), name="verify-otp"),

    path("token/refresh/", CustomTokenRefreshView.as_view(), name="token-refresh"),
]
