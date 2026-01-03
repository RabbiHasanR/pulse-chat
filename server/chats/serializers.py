from rest_framework import serializers

MIN_PART_SIZE = 5 * 1024 * 1024        # 5MB
MAX_PART_SIZE = 512 * 1024 * 1024      # 512MB (policy cap)
MAX_PARTS     = 10_000                 # S3 limit
DIRECT_THRESHOLD = 10 * 1024 * 1024    # 10MB
MAX_BATCH_COUNT  = 500

DEFAULT_EXPIRES_DIRECT = 300  # 5 minutes (Enough for small files)
DEFAULT_EXPIRES_PART = 3600   # 1 hour (Needed for large 5GB+ uploads)

class PrepareUploadIn(serializers.Serializer):
    # first call
    file_name = serializers.CharField(required=False)
    file_size = serializers.IntegerField(required=False, min_value=1)
    content_type = serializers.CharField(required=False)
    message_type = serializers.ChoiceField(choices=['image', 'video', 'file', 'audio'], required=False)
    receiver_id = serializers.IntegerField(required=False, min_value=1)

    client_part_size = serializers.IntegerField(required=False, min_value=MIN_PART_SIZE, max_value=MAX_PART_SIZE)
    client_num_parts = serializers.IntegerField(required=False, min_value=1)
    batch_count = serializers.IntegerField(required=False, min_value=1, max_value=MAX_BATCH_COUNT)

    # next-batch call
    upload_id = serializers.CharField(required=False)
    object_key = serializers.CharField(required=False)
    start_part = serializers.IntegerField(required=False, min_value=1)

    def validate(self, d):
        # next-batch path
        if d.get("upload_id"):
            if not d.get("object_key"):
                raise serializers.ValidationError({"object_key": "object_key is required with upload_id"})
            return d

        # first-call path
        required = ["file_name", "file_size", "content_type", "message_type", "receiver_id"]
        missing = [k for k in required if k not in d]
        if missing:
            raise serializers.ValidationError({"detail": f"Missing fields: {', '.join(missing)}"})

        file_size = int(d["file_size"])

        # direct -> no multipart fields required
        if file_size <= DIRECT_THRESHOLD:
            return d

        # multipart -> must include client chunking
        for k in ("client_part_size", "client_num_parts"):
            if k not in d:
                raise serializers.ValidationError({k: "This field is required for multipart uploads"})

        cps = int(d["client_part_size"])
        cnp = int(d["client_num_parts"])

        import math
        expected = math.ceil(file_size / cps)
        if cnp != expected:
            raise serializers.ValidationError({
                "client_num_parts": f"client_num_parts mismatch. Expected {expected} for file_size={file_size} and client_part_size={cps}"
            })
        if cnp > MAX_PARTS:
            raise serializers.ValidationError({
                "client_num_parts": f"Too many parts. Got {cnp}, max allowed is {MAX_PARTS}. Increase client_part_size."
            })
        return d



    

class CompleteUploadIn(serializers.Serializer):
    object_key = serializers.CharField()
    upload_id = serializers.CharField(required=False)
    parts = serializers.ListField(
        child=serializers.DictField(), required=False
    )

    def validate(self, data):
        parts = data.get("parts")
        upload_id = data.get("upload_id")

        if parts and not upload_id:
            raise serializers.ValidationError("upload_id is required when completing multipart upload.")

        if upload_id and not parts:
            raise serializers.ValidationError("parts are required when completing multipart upload.")

        return data
