import os
import uuid
import boto3
from django.conf import settings

# Get Env Vars
AWS_REGION = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
AWS_BUCKET = os.getenv("AWS_STORAGE_BUCKET_NAME", "test-bucket")
USE_S3_MOCK = os.getenv("USE_S3_MOCK") == "True"

# Configure Session
boto_config = {
    "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID"),
    "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY"),
    "region_name": AWS_REGION,
}

# 1. Create Resource/Client
if USE_S3_MOCK:
    # We are inside Docker, so we connect to the 's3mock' container
    endpoint = os.getenv("AWS_S3_ENDPOINT_URL", "http://s3mock:5000")
    
    s3 = boto3.client(
        "s3",
        **boto_config,
        endpoint_url=endpoint
    )
    
    # AUTO-CREATE BUCKET LOGIC
    # Moto forgets buckets when restarted, so we must recreate it on startup.
    try:
        s3.create_bucket(Bucket=AWS_BUCKET)
        print(f"✅ [MOCK] Bucket '{AWS_BUCKET}' created on {endpoint}")
    except Exception as e:
        # Ignore if it already exists
        pass

else:
    # Production / Real AWS
    session = boto3.session.Session(**boto_config)
    s3 = session.client("s3")


def new_object_key(user_id: int, file_name: str) -> str:
    safe = file_name.replace("/", "_")
    return f"chat_uploads/{user_id}/{uuid.uuid4()}/{safe}"



def generate_presigned_url(client_method, params, expires_in=3600):
    """
    Wrapper to fix Localhost vs Docker networking issues for Moto.
    """
    url = s3.generate_presigned_url(
        ClientMethod=client_method,
        Params=params,
        ExpiresIn=expires_in
    )

    # FIX: If we are mocking, replace internal docker host with localhost
    # so the browser can reach it.
    if USE_S3_MOCK:
        return url.replace("http://s3mock:5000", "http://localhost:5000")
    
    return url