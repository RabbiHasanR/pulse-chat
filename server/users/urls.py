from django.urls import path

from .views import (
    RegisterUserView,
    SendOTPView,
    VerifyOTPView,
    CustomTokenRefreshView,
    LogoutView,
    AddContactView,
    GetContactsView
)

urlpatterns = [
    path("register/", RegisterUserView.as_view(), name="register"),

    path("send-otp/", SendOTPView.as_view(), name="send-otp"),

    path("verify-otp/", VerifyOTPView.as_view(), name="verify-otp"),

    path("refresh-token/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    
    path("logout/", LogoutView.as_view(), name="logout"),
    
    path('get_contacts/', GetContactsView.as_view(), name='get-contacts'),
    
    path('add_contacts/', AddContactView.as_view(), name='add-contacts'),
    
]
