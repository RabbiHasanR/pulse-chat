from utils.response import error_response
from rest_framework.views import exception_handler

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return error_response(
            message=str(exc),
            errors=response.data,
            status=response.status_code
        )
    return error_response("Internal server error", status=500)
