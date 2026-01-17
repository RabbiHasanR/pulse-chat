from django.urls import path
from .views import PrepareUpload, CompleteUpload, ChatListView

urlpatterns = [
    path("upload/prepare/", PrepareUpload.as_view(), name="chat-media-prepare"),

    path("upload/complete/", CompleteUpload.as_view(), name="chat-media-complete"),
    
    path('list/', ChatListView.as_view(), name='chat-list'),
]