import os, uuid, boto3

AWS_REGION = os.getenv("AWS_S3_REGION_NAME")
AWS_BUCKET = os.getenv("AWS_STORAGE_BUCKET_NAME")

session = boto3.session.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=AWS_REGION,
)
s3 = session.client("s3")

def new_object_key(user_id: int, file_name: str) -> str:
    safe = file_name.replace("/", "_")
    return f"chat_uploads/{user_id}/{uuid.uuid4()}/{safe}"
