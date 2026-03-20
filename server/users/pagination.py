from utils.pagination import BaseCursorPagination

class ContactCursorPagination(BaseCursorPagination):
    ordering = '-created_at'
    data_key = 'contacts'
    success_message = 'Contacts retrieved successfully'


class UserCursorPagination(BaseCursorPagination):
    ordering = '-date_joined'
    data_key = 'users'
    success_message = 'Users retrieved successfully'