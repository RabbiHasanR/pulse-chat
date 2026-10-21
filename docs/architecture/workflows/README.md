# Pulse Chat — Architecture Workflows

Detailed sequence diagrams and protocol walkthroughs for core asynchronous flows in Pulse Chat.

## Workflow Guides

| Workflow | Description |
|:---|:---|
| [audio-workflow.md](audio-workflow.md) | Audio recording upload, direct-to-S3 signed PUT, and duration/waveform extraction. |
| [hls-workflow.md](hls-workflow.md) | Video HLS segmented playlist generation (`master.m3u8`, variant playlists, `.ts` chunks). |
| [media-message-workflow.md](media-message-workflow.md) | End-to-end media message lifecycle (presigned URLs, S3 direct upload, completion trigger). |
| [video-processing-workflow.md](video-processing-workflow.md) | FFmpeg worker pipeline, thumbnail extraction, resolution transcoding, and status notifications. |
| [websocket-workflow.md](websocket-workflow.md) | Real-time WebSocket connection handshake, JWT binding, Redis channel layer group join, and event loop. |
| [frontend-integration-album.md](frontend-integration-album.md) | Client-side fan-out upload integration guide for batch/album attachments. |
