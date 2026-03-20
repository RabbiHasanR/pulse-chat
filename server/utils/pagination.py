from rest_framework.pagination import CursorPagination
from utils.response import success_response

class BaseCursorPagination(CursorPagination):
    page_size = 10

    data_key: str = None
    success_message: str = None

    def get_paginated_response(self, data):
        return success_response(
            message=self.success_message,
            data={
                self.data_key: data,
                "next": self.get_next_link(),
                "previous": self.get_previous_link()
            },
            status=200
        )
