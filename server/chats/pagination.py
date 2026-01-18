from rest_framework.pagination import CursorPagination
from utils.response import success_response

class ChatListCursorPagination(CursorPagination):
    page_size = 20
    # Sort by 'updated_at' so the most active chats appear at the top
    ordering = '-updated_at' 
    
    def get_paginated_response(self, data):
        """
        Custom response format to match your project's 'success_response' wrapper.
        """
        return success_response(
            message="Chats retrieved successfully",
            data={
                "conversations": data,
                "next": self.get_next_link(),
                "previous": self.get_previous_link()
            },
            status=200
        )
        


class MessageCursorPagination(CursorPagination):
    page_size = 30
    ordering = '-created_at' # Newest messages first
    
    def get_paginated_response(self, data):
        return success_response(
            message="Messages retrieved successfully",
            data={
                "messages": data,
                "next": self.get_next_link(),
                "previous": self.get_previous_link()
            },
            status=200
        )