from django.urls import path
from .views import PrepareUpload, CompleteUpload, ChatListView, ChatMessageListView

urlpatterns = [
    path("upload/prepare/", PrepareUpload.as_view(), name="chat-media-prepare"),

    path("upload/complete/", CompleteUpload.as_view(), name="chat-media-complete"),
    
    path('list/', ChatListView.as_view(), name='chat-list'),
    path('user/<int:partner_id>/messages/', ChatMessageListView.as_view(), name='chat-thread-messages'),
]