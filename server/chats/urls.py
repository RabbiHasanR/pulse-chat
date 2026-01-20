from django.urls import path
from .views import SendMessageView, SignBatchView, CompleteUpload, ChatListView, ChatMessageListView

urlpatterns = [
    path('send/', SendMessageView.as_view(), name='send-message'),
    
    # 2. Upload Management
    path('upload/sign-batch/', SignBatchView.as_view(), name='upload-sign-batch'),

    path("upload/complete/", CompleteUpload.as_view(), name="chat-media-complete"),
    
    path('list/', ChatListView.as_view(), name='chat-list'),
    path('user/<int:partner_id>/messages/', ChatMessageListView.as_view(), name='chat-thread-messages'),
]