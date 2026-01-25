import boto3
import uuid
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

# --- 1. CONFIGURATION CONSTANTS ---

# We read from Django settings to ensure consistency with .env and Docker
AWS_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_S3_REGION_NAME
AWS_BUCKET = settings.AWS_STORAGE_BUCKET_NAME
USE_MOCK = settings.USE_S3_MOCK
MOCK_ENDPOINT = settings.AWS_S3_ENDPOINT_URL

# Upload Thresholds & Limits
DIRECT_THRESHOLD = 5 * 1024 * 1024  # 5MB
DEFAULT_EXPIRES_DIRECT = 300        # 5 Minutes
DEFAULT_EXPIRES_PART = 3600         # 1 Hour
MAX_BATCH_COUNT = 500               # Security Cap

# --- 2. S3 CLIENT INITIALIZATION ---

# Custom Config for Signature Version 4 (Required for Presigned URLs)
my_config = Config(
    region_name=AWS_REGION,
    signature_version='s3v4',
    retries={'max_attempts': 3, 'mode': 'standard'}
)

# Initialize Client
# Logic: If USE_S3_MOCK is True, we pass the 'endpoint_url'. 
# If False (Production), we pass None, letting boto3 use real AWS URLs.
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
    config=my_config,
    endpoint_url=MOCK_ENDPOINT if USE_MOCK else None
)

# --- 3. MOTO / LOCAL MOCK SETUP (The "Magic" Part) ---

if USE_MOCK:
    print(f"🛠️  [S3 UTILS] Running in MOCK Mode. Endpoint: {MOCK_ENDPOINT}")
    
    # A. Auto-Create Bucket
    # Moto starts empty every time. We must create the bucket or uploads will fail.
    try:
        s3.head_bucket(Bucket=AWS_BUCKET)
    except ClientError:
        print(f"⚠️  [S3 UTILS] Bucket '{AWS_BUCKET}' not found. Creating it...")
        s3.create_bucket(Bucket=AWS_BUCKET)

    # B. Define Wrapper for Hostname Fix
    # Docker sees 'http://s3mock:5000', but your Host OS (Postman/Browser) 
    # needs 'http://localhost:5000'.
    def generate_presigned_url(ClientMethod, Params, ExpiresIn):
        url = s3.generate_presigned_url(
            ClientMethod=ClientMethod,
            Params=Params,
            ExpiresIn=ExpiresIn
        )
        # Rewrite the domain for local access
        return url.replace("s3mock:5000", "localhost:5000")

else:
    # PRODUCTION: Pass-through wrapper (Do nothing)
    def generate_presigned_url(ClientMethod, Params, ExpiresIn):
        return s3.generate_presigned_url(
            ClientMethod=ClientMethod,
            Params=Params,
            ExpiresIn=ExpiresIn
        )


# --- 4. HELPER FUNCTIONS ---

def new_object_key(user_id, filename):
    """
    Generates a secure, unique, and organized file path for S3.
    Structure: uploads/user_{id}/{uuid}/{filename}
    """
    unique_id = str(uuid.uuid4())
    # Basic sanitization to prevent weird path issues
    clean_filename = filename.replace(" ", "_").replace("/", "")
    
    return f"uploads/user_{user_id}/{unique_id}/{clean_filename}"

# Monkey-patch the custom generator onto the client object for convenience
# (Optional, but makes imports cleaner in other files)
s3.generate_presigned_url_custom = generate_presigned_url