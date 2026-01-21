import os
import boto3
import uuid
from botocore.config import Config

# --- 1. CONFIGURATION CONSTANTS ---
# AWS Credentials (Load from Environment Variables)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_BUCKET = os.getenv("AWS_BUCKET_NAME")

# Upload Thresholds & Limits
# 5MB: Files smaller than this use "put_object" (Single HTTP Request)
# Files larger than this use "create_multipart_upload" (Chunked)
DIRECT_THRESHOLD = 5 * 1024 * 1024  

# Expiration Times (Seconds)
# 5 Minutes: Direct uploads are fast, so the link shouldn't live long.
DEFAULT_EXPIRES_DIRECT = 300 

# 1 Hour: Multipart parts can take longer to upload, so we give more time.
DEFAULT_EXPIRES_PART = 3600    

# Security Cap
# Max number of presigned URLs to generate in one batch request.
# Prevents a malicious user from asking for 100,000 URLs and freezing the server.
MAX_BATCH_COUNT = 500          

# --- 2. S3 CLIENT INITIALIZATION ---
# We use a custom config to ensure the signature version is compatible 
# with presigned URLs (v4 is standard).
my_config = Config(
    region_name=AWS_REGION,
    signature_version='s3v4',
    retries={
        'max_attempts': 3,
        'mode': 'standard'
    }
)

s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
    config=my_config
)

# --- 3. HELPER FUNCTIONS ---

def new_object_key(user_id, filename):
    """
    Generates a secure, unique, and organized file path for S3.
    Structure: uploads/user_{id}/{uuid}/{filename}
    
    Why this structure?
    1. 'uploads/': Keeps root clean.
    2. 'user_{id}/': logical separation (easier to delete user data later).
    3. '{uuid}/': Prevents overwriting if user uploads "image.jpg" twice.
    4. '{filename}': Preserves original extension and name for metadata.
    """
    unique_id = str(uuid.uuid4())
    # Sanitize filename (basic check)
    clean_filename = filename.replace(" ", "_").replace("/", "")
    
    return f"uploads/user_{user_id}/{unique_id}/{clean_filename}"