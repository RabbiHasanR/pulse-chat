from rest_framework.pagination import CursorPagination
from utils.response import success_response, error_response

class ContactCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-created_at'
    
    def get_paginated_response(self, data):
        return success_response(
            message="Contacts retrieved successfully",
            data={
                "contacts": data,
                "next": self.get_next_link(),
                "previous": self.get_previous_link()
            },
            status_code=200
        )
    

class UserCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-date_joined'
    
    def get_paginated_response(self, data):
        return success_response(
            message="Users retrieved successfully",
            data={
                "users": data,
                "next": self.get_next_link(),
                "previous": self.get_previous_link()
            },
            status_code=200
        )
