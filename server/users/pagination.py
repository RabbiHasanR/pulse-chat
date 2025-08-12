from rest_framework.pagination import CursorPagination

class ContactCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-created_at'
    

class UserCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-date_joined'
