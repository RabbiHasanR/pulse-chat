from utils.response import error_response
from rest_framework.views import exception_handler

import traceback

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return error_response(
            message=str(exc),
            errors=response.data,
            status=response.status_code
        )
        
    # --- ADD THIS DEBUG BLOCK ---
    print("\n!!! SERVER CRASH DETECTED !!!")
    print(f"Exception Type: {type(exc)}")
    print(f"Exception Message: {exc}")
    traceback.print_exc() # <--- This prints the line number where it crashed
    # ----------------------------
    
    return error_response("Internal server error", status=500)
