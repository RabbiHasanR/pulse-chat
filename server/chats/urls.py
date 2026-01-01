from django.urls import path
from .views import PrepareUpload, CompleteUpload

urlpatterns = [
    path("upload/prepare/", PrepareUpload.as_view(), name="chat-media-prepare"),

    path("upload/complete/", CompleteUpload.as_view(), name="chat-media-complete"),
]