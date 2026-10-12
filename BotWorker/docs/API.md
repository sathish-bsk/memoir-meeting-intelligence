# BotWorker API Reference

## Endpoints

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "state": "idle"
}
```

**Example:**
```bash
curl http://localhost:8000/health
```

---

### GET /status

Get current bot status including transcript when available.

**Response:**
```json
{
  "job_id": "abc-123",
  "state": "in_meeting",
  "meeting_url": "https://meet.google.com/xxx-yyyy-zzz",
  "joined_at": "2024-01-15T10:30:00Z",
  "chunks_saved": 5,
  "uptime_seconds": 180.5,
  "transcript": null,
  "error": null
}
```

**States:** `idle`, `launching_browser`, `navigating`, `waiting_room`, `in_meeting`, `meeting_ended`, `transcribing`, `cleaning_up`, `completed`, `error`

**Example:**
```bash
curl http://localhost:8000/status
```

---

### POST /join

Start joining a Google Meet meeting. Launches the full lifecycle asynchronously.

**Request body:**
```json
{
  "job_id": "abc-123",
  "meeting_url": "https://meet.google.com/xxx-yyyy-zzz",
  "bot_name": "Meeting Notetaker"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_id` | string | yes | Unique identifier for this job |
| `meeting_url` | string | yes | Google Meet URL (must match `meet.google.com/xxx-xxxx-xxx`) |
| `bot_name` | string | no | Name shown in meeting (default: from config) |

**Response (200):**
```json
{
  "status": "accepted",
  "job_id": "abc-123"
}
```

**Errors:**
- `400` — Invalid meeting URL
- `409` — Bot is busy (already in a meeting)

**Example:**
```bash
curl -X POST http://localhost:8000/join \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-1", "meeting_url": "https://meet.google.com/abc-defg-hij"}'
```

---

### POST /stop

Stop the current meeting session. Signals the lifecycle to begin shutdown.

**Request body (optional):**
```json
{
  "reason": "manual_stop"
}
```

**Response (200):**
```json
{
  "status": "stopping",
  "job_id": "abc-123"
}
```

**Errors:**
- `400` — No active meeting

**Example:**
```bash
curl -X POST http://localhost:8000/stop \
  -H "Content-Type: application/json" \
  -d '{"reason": "user_requested"}'
```

---

## Event Types

Events are sent to `{BACKEND_URL}/api/bots/events` as POST requests.

| Event | When | Data |
|-------|------|------|
| `join_started` | Begin join flow | `{meeting_url}` |
| `waiting_room_entered` | In waiting room | `{}` |
| `joined_meeting` | Admitted to meeting | `{}` |
| `recording_started` | FFmpeg running | `{}` |
| `chunk_saved` | Chunk saved to storage | `{chunk_index, storage_key, file_size_bytes}` |
| `heartbeat` | Every 30s in meeting | `{}` |
| `meeting_ended` | Meeting ended | `{reason}` |
| `transcription_started` | Merging + transcribing | `{total_chunks}` |
| `transcription_complete` | Transcript ready | `{transcript}` |
| `transcription_failed` | Transcription error | `{error}` |
| `bot_denied` | Entry denied | `{}` |
| `bot_kicked` | Removed from meeting | `{}` |
| `bot_error` | Unrecoverable error | `{error}` |
| `cleanup_complete` | All resources freed | `{total_chunks}` |

**Event payload format:**
```json
{
  "job_id": "abc-123",
  "event_type": "chunk_saved",
  "timestamp": "2024-01-15T10:30:30Z",
  "data": {
    "chunk_index": 5,
    "storage_key": "abc-123/chunk_0005.ogg",
    "file_size_bytes": 24000
  }
}
```
