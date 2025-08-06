from django.urls import path

from .views import (
    RegisterUserView,
    SendOTPView,
    VerifyOTPView,
    CustomTokenRefreshView,
    LogoutView
)

urlpatterns = [
    path("register/", RegisterUserView.as_view(), name="register"),

    path("send-otp/", SendOTPView.as_view(), name="send-otp"),

    path("verify-otp/", VerifyOTPView.as_view(), name="verify-otp"),

    path("refresh-token/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    
    path("logout/", LogoutView.as_view(), name="logout"),
    
]
