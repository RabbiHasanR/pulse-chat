Here is the **Frontend Integration Guide** for the "Album" (Batch Upload) feature. This guide details exactly how the UI should structure requests, handle parallel uploads, and render the chat grid.

### **Overview: The "Fan-Out" Strategy**

Instead of uploading files one by one (Linear), the UI will:

1. **Request:** Send **one** batch request to the API.
2. **Fan-Out:** Receive multiple S3 URLs and start **all** uploads simultaneously.
3. **Fan-In:** Notify the server individually as each file finishes.

---

### **Step 1: The Batch Request (UI  API)**

When the user selects 3 files (e.g., `beach.jpg`, `party.mp4`, `document.pdf`) and clicks send.

**Endpoint:** `POST /api/v1/files/upload/` (or your configured route)
**Headers:** `Authorization: Bearer <token>`

**The Payload (JSON):**
The UI constructs a single object containing the `attachments` array.

```json
{
  "receiver_id": 42,
  "text": "Check out the photos from the trip! 🌴", 
  "attachments": [
    {
      "file_name": "beach.jpg",
      "file_size": 3050000, 
      "content_type": "image/jpeg",
      "kind": "image"
    },
    {
      "file_name": "party.mp4",
      "file_size": 15000000, 
      "content_type": "video/mp4",
      "kind": "video",
      "client_part_size": 5242880,  // Required for multipart (>5MB)
      "client_num_parts": 3
    },
    {
      "file_name": "itinerary.pdf",
      "file_size": 102400, 
      "content_type": "application/pdf",
      "kind": "file"
    }
  ]
}

```

---

### **Step 2: Handle the Response (API  UI)**

The server creates the Message and Assets immediately. It returns a list of **Upload Instructions**.

**Response (201 Created):**

```json
{
  "message_id": 105,
  "uploads": [
    {
      "asset_id": 501,              // <--- Important: Link this to File 1
      "mode": "direct",
      "put_url": "https://s3.aws.com/..."
    },
    {
      "asset_id": 502,              // <--- Link this to File 2
      "mode": "multipart",
      "upload_id": "upload_xyz_123",
      "batch": { "items": [...] }
    },
    {
      "asset_id": 503,              // <--- Link this to File 3
      "mode": "direct",
      "put_url": "https://s3.aws.com/..."
    }
  ]
}

```

**UI Action:**

1. **Immediate Render:** Do not wait for uploads! Add a "Pending Message" bubble to the chat list.
2. **Render Grid:** Show a grid with 3 items. Use placeholders (spinners or blur-hashes) for now.
3. **Map Files:** Match your local JavaScript `File` objects to the response `uploads` array (by index or name).

---

### **Step 3: Parallel Execution (UI  S3)**

The UI must now start `N` separate upload jobs in parallel.

**Pseudo-Code (React/JS Logic):**

```javascript
// Assume 'files' is your array of JS File objects
// Assume 'instructions' is the 'uploads' array from the API response

const uploadPromises = files.map((file, index) => {
    const instruction = instructions[index]; 
    const assetId = instruction.asset_id;

    // 1. Direct Upload (Simple)
    if (instruction.mode === 'direct') {
        return fetch(instruction.put_url, {
            method: 'PUT',
            body: file,
            headers: { 'Content-Type': file.type }
        }).then(() => {
            // Success! Notify Backend
            return completeUpload(assetId, instruction.object_key);
        });
    } 

    // 2. Multipart Upload (Complex)
    else {
        // Use your existing chunking logic here to upload parts to S3
        return uploadPartsToS3(file, instruction).then((partsEtag) => {
            // Success! Notify Backend
            return completeUpload(assetId, instruction.object_key, partsEtag, instruction.upload_id);
        });
    }
});

// We don't necessarily need to wait for Promise.all() 
// because we want each image to turn "green" individually.

```

---

### **Step 4: Individual Completion (UI  API)**

As **soon as one file finishes**, tell the server. Do not wait for the others.

**Endpoint:** `POST /api/v1/files/upload/complete/`

**Request (For File 1 - Image):**

```json
{
  "object_key": "raw/user_1/beach.jpg",
  // No upload_id needed for direct upload
}

```

**Request (For File 2 - Video):**

```json
{
  "object_key": "raw/user_1/party.mp4",
  "upload_id": "upload_xyz_123",
  "parts": [ { "ETag": "...", "PartNumber": 1 }, ... ]
}

```

**Why separate calls?**

* If the Image finishes in 2 seconds, the server starts processing it immediately.
* The UI receives a WebSocket event saying "Image 1 is Ready".
* The User sees Image 1 appear clearly in the grid, while the Video is still loading.

---

### **Step 5: Updating the UI (WebSocket  UI)**

The UI must listen for `chat_message_update` events. The key is using `asset_id` to update the specific cell in the grid.

**Event Payload:**

```json
{
  "type": "chat_message_update",
  "data": {
    "message_id": 105,
    "asset_id": 501,          // <--- Target specific item in the grid
    "stage": "thumbnail_ready",
    "thumbnail_url": "https://s3.../thumb.webp",
    "progress": 100
  }
}

```

**UI Reducer Logic:**

1. Find message `#105` in the chat list.
2. Inside that message, find the asset with `id: 501`.
3. Update its state: `loading: false`, `url: thumbnail_url`.
4. Leave asset `#502` (Video) as `loading: true`.

---

### **Summary of UX States**

| Timeline | Action | UI State (The Grid) |
| --- | --- | --- |
| **0s** | User Clicks Send | Message appears. 3 Grey boxes with spinners. Text caption visible. |
| **2s** | Image 1 Uploads | **Box 1:** "Processing..." (Blue spinner). **Box 2,3:** Uploading... |
| **3s** | Image 1 Processed | **Box 1:** Shows Photo. **Box 2,3:** Still uploading. |
| **10s** | Video Uploads | **Box 2:** "Processing..." (Blue spinner). |
| **15s** | Video Playable | **Box 2:** Shows Play Button. |

This provides the exact snappy, "Telegram-like" feel where images pop in one by one.