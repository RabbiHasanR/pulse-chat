### **System Overview**

This system uses a **Client-Direct-to-S3** architecture.

* **Django Server:** Acts as a "Controller". It authorizes uploads, tracks file metadata in the DB, and coordinates notifications. It **never** touches the actual file bytes.
* **Frontend Client:** Does the heavy lifting. It slices files, uploads bytes to S3, and reports back.
* **AWS S3:** Stores the raw files.
* **Celery/Redis:** Handles background processing (thumbnails, encoding) and real-time WebSocket updates.

---

### **1. The Thresholds (Policy)**

Your code defines three distinct behaviors based on file size:

| File Size | Mode | Description |
| --- | --- | --- |
| **0 - 10 MB** | **Direct Upload** | Simple, single PUT request. Fastest for images/audio. |
| **10 MB - 2.5 GB** | **Multipart** | File split into chunks (5MB+). Uploaded in parallel. |
| **> 2.5 GB** | **Multipart + Paged** | Too many chunks to sign at once. URLs are fetched in batches (500 at a time). |

*(Note: 2.5GB comes from 5MB part size × 500 max batch count)*

---

### **2. Workflow A: Small Files (Direct Mode)**

**Example:** Sending a 4MB Photo (`image.jpg`)

1. **Prepare (Frontend -> Backend)**
* **Request:** `POST /prepare/` with `{ file_size: 4MB }`.
* **Backend:** Creates `ChatMessage` (Pending) & `MediaAsset` (Queued).
* **Notify:** Sends WebSocket event: `stage: "uploading"`.
* **Response:** Returns `mode: "direct"` and **ONE** Presigned PUT URL.


2. **Transfer (Frontend -> S3)**
* **Action:** Frontend sends `PUT` request to the S3 URL with the file blob.
* **Headers:** `Content-Type: image/jpeg`.


3. **Complete (Frontend -> Backend)**
* **Request:** `POST /complete/` with `{ object_key: "..." }`.
* **Backend:** Verifies asset exists. Triggers `process_uploaded_asset` task.
* **Notify:** Sends WebSocket event: `stage: "processing"`.


4. **Processing (Worker -> UI)**
* **Task:** Worker processes file -> Sets `status: sent`, `stage: "done"`.
* **Notify:** WebSocket event with `media_url`. UI shows image.



---

### **3. Workflow B: Medium/Big Files (Multipart Mode)**

**Example:** Sending a 50MB Video (`video.mp4`)
*Configuration:* `client_part_size` = 5MB. Total Parts = 10.

1. **Prepare (Frontend -> Backend)**
* **Request:** `POST /prepare/` with `{ file_size: 50MB, client_num_parts: 10 }`.
* **Backend:**
* Initiates S3 Multipart Upload -> Gets `UploadId`.
* Generates **10 Presigned URLs** (Part 1 to 10).


* **Response:** Returns `mode: "multipart"`, `upload_id`, and array of 10 URLs.


2. **Transfer (Frontend -> S3)**
* **Action:** Frontend uses `File.slice()` to cut the video into 10 chunks.
* **Loop:** Uploads Chunk 1 to URL 1, Chunk 2 to URL 2...
* **Track:** Frontend saves the **ETag** (receipt header) from every S3 response.


3. **Complete (Frontend -> Backend)**
* **Request:** `POST /complete/` with:
```json
{
  "upload_id": "ABC...",
  "parts": [{"PartNumber": 1, "ETag": "tag1"}, ...]
}

```


* **Backend:** Calls `s3.complete_multipart_upload`. S3 stitches the file.
* **Processing:** (Same as Workflow A).



---

### **4. Workflow C: Huge Files (Pagination Mode)**

**Example:** Sending a 10GB Movie (`movie.mkv`)
*Configuration:* `client_part_size` = 5MB. Total Parts = 2000.
*Constraint:* `MAX_BATCH_COUNT` = 500.

1. **Prepare (First Batch)**
* **Request:** `POST /prepare/` with `{ client_num_parts: 2000 }`.
* **Backend:** Generates URLs for **Parts 1 - 500**.
* **Response:** Returns `batch: { start: 1, count: 500, items: [...] }`.


2. **Transfer Batch 1**
* Frontend uploads Parts 1-500.


3. **Request Next Batch (The Loop)**
* **Request:** `POST /prepare/` (Same endpoint!)
```json
{
   "upload_id": "ABC...",
   "object_key": "...",
   "start_part": 501,
   "batch_count": 500
}

```


* **Backend:** Generates URLs for **Parts 501 - 1000**.
* *Repeat this step until all 2000 parts are uploaded.*


4. **Complete**
* **Request:** `POST /complete/` with the huge list of 2000 ETags.
* **Backend:** Assembles the file on S3.



---

### **5. Status & UI Lifecycle (The "Truth" Table)**

This table defines what the user sees at every second of the process.

| Step | Action | Message Status | Processing Status | UI Stage | **Frontend Shows** |
| --- | --- | --- | --- | --- | --- |
| 1 | **User Selects File** | (Local) | - | - | **Local Preview + Spinner** |
| 2 | **Prepare API** | `pending` | `queued` | `uploading` | **"Incoming..." / Loading** |
| 3 | **Uploading...** | `pending` | `queued` | `uploading` | **Progress Bar %** |
| 4 | **Complete API** | `pending` | `queued` | `processing` | **"Processing..." / Clock Icon** |
| 5 | **Worker Running** | `pending` | `running` | `processing` | **"Processing... 45%"** (if video) |
| 6 | **Worker Success** | **`sent`** | **`done`** | **`done`** | **Actual Image/Video + Tick** |
| 7 | **Worker Failed** | `pending` | `failed` | `failed` | **Red Error Icon (Retry)** |

---

### **6. Key Code References (Where logic lives)**

* **`views.py` / `PrepareUpload**`: Decides between Direct vs Multipart. Handles the "Batching" logic for huge files.
* **`views.py` / `CompleteUpload**`: Finalizes the S3 file. Triggers the worker task. Sends the "Processing Started" notification.
* **`tasks.py` / `process_uploaded_asset**`: The heavy lifter. Simulates work, updates progress, sets final status to `done`, and marks message as `sent`.
* **`tasks.py` / `notify_message_event**`: The gatekeeper. Ensures "Seen" status is only updated if the file is truly ready (`processing_status='done'`).