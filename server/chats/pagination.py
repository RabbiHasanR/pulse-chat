from utils.pagination import BaseCursorPagination

class ChatListCursorPagination(BaseCursorPagination):
    page_size = 20
    ordering = '-updated_at'
    data_key = 'conversations'
    success_message = 'Chats retrieved successfully'


class MessageCursorPagination(BaseCursorPagination):
    page_size = 30
    ordering = '-created_at'
    data_key = 'messages'
    success_message = 'Messages retrieved successfully'