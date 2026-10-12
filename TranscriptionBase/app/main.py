"""
Transcription Service API
FastAPI application for audio/video transcription.
"""
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import setup_logging, get_request_logger
from app.services.cleanup import cleanup_temp_dir
from app.services.ingest import (
    create_request_temp_dir,
    ingest_upload,
    ingest_media_url,
    ingest_youtube,
    IngestError,
)
from app.services.ffmpeg import normalize_audio, FFmpegError
from app.services.asr import transcribe_audio, ASRError
from app.services.asr_pingala import transcribe_audio_pingala, PingalaASRError
from app.services.asr_ai4bharat import transcribe_audio_ai4bharat, AI4BharatASRError
from app.services.audio_preprocessor import preprocess_audio, PreprocessError
from app.audio_preprocess.router import router as audio_preprocess_router
from app.job_processor.router import router as job_processor_router

from app.job_processor.db import init_db as init_job_db, close_db as close_job_db

# Initialize logging
setup_logging()

# Create FastAPI app
app = FastAPI(
    title="Transcription Service",
    description="Audio/Video transcription API using Whisper",
    version="1.0.0",
)

# Mount audio preprocessing A/B comparison router
app.include_router(audio_preprocess_router)

# Mount job processor router (transcription job queue API)
app.include_router(job_processor_router)


@app.on_event("startup")
async def startup_event():
    await init_job_db()

    # LangChain provider registry — Phase 7a (plan §9.3). Builds the
    # per-backend instances from credentials present in env. Cheap and
    # idempotent. Failures don't block startup (provider just won't be
    # registered, router falls back to env default at call time).
    try:
        from app.llm_observability.provider_registry import init_registry
        init_registry()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "LangChain provider registry init failed (non-fatal): %s", e,
        )

    # Optional Hub prompt prewarm — Phase 2.1 (plan §7). Only fires when
    # LANGCHAIN_PREWARM_ON_STARTUP=true; otherwise prompts are pulled
    # lazily on first call.
    try:
        from app.audio_preprocess.config import preprocess_config as _cfg
        if _cfg.LANGCHAIN_PREWARM_ON_STARTUP and _cfg.LANGCHAIN_ENABLED:
            from app.llm_observability import prewarm_all
            prewarm_all([
                "memoir-classification", "memoir-crm-extraction",
                "memoir-intelligence", "memoir-pmf", "memoir-p5-summary",
                "memoir-p5-reduce", "memoir-faq",
                "memoir-corrector", "memoir-punctuation",
                "memoir-extractor",
            ])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Hub prewarm failed (non-fatal): %s", e)


@app.on_event("shutdown")
async def shutdown_event():
    await close_job_db()


@app.get("/internal/llm-registry")
async def llm_registry_status():
    """Diagnostic — returns which LLM providers are registered (Phase 7a, plan §10.5).

    Use this after vast-start.sh to verify the cross-provider router is
    configured correctly. Does NOT expose secrets.
    """
    from app.llm_observability import registry_snapshot
    return registry_snapshot()


# Request models
class TranscribeRequest(BaseModel):
    youtube_url: Optional[str] = None
    media_url: Optional[str] = None
    output_language: Optional[str] = None  # "en" for English/romanized, "auto" or None for auto-detect
    asr_model: Optional[str] = "whisper"  # "whisper" or "pingala"
    preprocess: Optional[bool] = False  # Enable audio preprocessing pipeline
    noise_method: Optional[str] = "noisereduce"  # "noisereduce" or "deepfilternet"


# HTML Test Page (inline for simplicity)
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Transcription Service</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        h1 { color: #333; }
        .section {
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .section h2 {
            margin-top: 0;
            color: #555;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: 600;
            color: #444;
        }
        input[type="text"], input[type="url"], input[type="file"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        button {
            background: #007bff;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-right: 10px;
            margin-bottom: 10px;
        }
        button:hover { background: #0056b3; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        button.secondary {
            background: #6c757d;
        }
        button.secondary:hover { background: #545b62; }
        button.success {
            background: #28a745;
        }
        button.success:hover { background: #1e7e34; }
        #result {
            margin-top: 20px;
            padding: 15px;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            white-space: pre-wrap;
            word-break: break-word;
            font-family: monospace;
            font-size: 13px;
            max-height: 400px;
            overflow-y: auto;
        }
        .status {
            padding: 8px 12px;
            border-radius: 4px;
            margin-bottom: 10px;
            font-weight: 600;
        }
        .status.loading { background: #fff3cd; color: #856404; }
        .status.success { background: #d4edda; color: #155724; }
        .status.error { background: #f8d7da; color: #721c24; }
        .request-id { font-size: 12px; color: #666; margin-top: 5px; }
        .tabs {
            display: flex;
            border-bottom: 2px solid #dee2e6;
            margin-bottom: 15px;
        }
        .tab {
            padding: 10px 20px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 14px;
            color: #666;
            margin: 0;
        }
        .tab.active {
            color: #007bff;
            border-bottom: 2px solid #007bff;
            margin-bottom: -2px;
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        select {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            background: white;
        }
        .language-selector {
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #eee;
        }
    </style>
</head>
<body>
    <h1>🎙️ Transcription Service</h1>
    <p>Convert audio/video to text using AI. <a href="/docs" target="_blank">API Docs</a></p>

    <div class="section">
        <h2>Transcribe</h2>
        <div class="language-selector">
            <label for="asr_model">ASR Model:</label>
            <select id="asr_model">
                <option value="whisper" selected>Whisper (OpenAI)</option>
                <option value="pingala">Pingala V1 Universal (204 languages)</option>
                <option value="ai4bharat">AI4Bharat (Tamil Romanized)</option>
            </select>
        </div>
        <div class="language-selector">
            <label for="output_language">Output Language:</label>
            <select id="output_language">
                <option value="en" selected>English (Romanized)</option>
                <option value="auto">Auto-detect (Native Script)</option>
            </select>
        </div>
        <div class="language-selector">
            <label style="display:inline;font-weight:normal;cursor:pointer;">
                <input type="checkbox" id="preprocess" style="margin-right:6px;" onchange="toggleNoiseMethod()">
                Enable Audio Preprocessing (noise reduction, silence trimming, loudness normalization)
            </label>
        </div>
        <div class="language-selector" id="noise_method_container" style="display:none;">
            <label for="noise_method">Noise Reduction Method:</label>
            <select id="noise_method">
                <option value="noisereduce">Spectral Gating (lightweight, stationary noise)</option>
                <option value="deepfilternet">DeepFilterNet3 (neural, all noise types)</option>
            </select>
        </div>
        <div class="tabs">
            <button class="tab active" onclick="showTab('upload')">File Upload</button>
            <button class="tab" onclick="showTab('media')">Media URL</button>
            <button class="tab" onclick="showTab('youtube')">YouTube</button>
        </div>

        <div id="tab-upload" class="tab-content active">
            <label for="file">Select audio/video file:</label>
            <input type="file" id="file" accept=".mp3,.mp4,.m4a,.wav,.webm,.ogg,.flac">
            <button onclick="submitFile()">Submit</button>
        </div>

        <div id="tab-media" class="tab-content">
            <label for="media_url">Direct URL to audio/video:</label>
            <input type="url" id="media_url" placeholder="https://example.com/audio.mp3">
            <button onclick="submitMediaUrl()">Submit</button>
        </div>

        <div id="tab-youtube" class="tab-content">
            <label for="youtube_url">YouTube URL:</label>
            <input type="url" id="youtube_url" placeholder="https://www.youtube.com/watch?v=...">
            <button onclick="submitYoutube()">Submit</button>
        </div>
    </div>

    <div class="section">
        <h2>Concurrency Test</h2>
        <p>Test concurrent request handling:</p>
        <button class="success" onclick="runParallelRequests()">Run 2 Parallel Requests</button>
        <button class="secondary" onclick="checkHealth()">Health Check</button>
    </div>

    <div class="section">
        <h2>Result</h2>
        <div id="status" class="status" style="display:none;"></div>
        <div id="result">Results will appear here...</div>
    </div>

    <script>
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelector(`[onclick="showTab('${tabName}')"]`).classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
        }

        function setStatus(type, message) {
            const status = document.getElementById('status');
            status.style.display = 'block';
            status.className = `status ${type}`;
            status.textContent = message;
        }

        function setResult(text) {
            document.getElementById('result').textContent = text;
        }

        function getSelectedLanguage() {
            return document.getElementById('output_language').value;
        }

        function getSelectedModel() {
            return document.getElementById('asr_model').value;
        }

        function getPreprocess() {
            return document.getElementById('preprocess').checked;
        }

        function getNoiseMethod() {
            return document.getElementById('noise_method').value;
        }

        function toggleNoiseMethod() {
            const container = document.getElementById('noise_method_container');
            container.style.display = document.getElementById('preprocess').checked ? 'block' : 'none';
        }

        async function submitFile() {
            const file = document.getElementById('file').files[0];
            if (!file) {
                setStatus('error', 'Please select a file');
                return;
            }

            setStatus('loading', 'Uploading and processing...');
            const formData = new FormData();
            formData.append('file', file);
            formData.append('output_language', getSelectedLanguage());
            formData.append('asr_model', getSelectedModel());
            formData.append('preprocess', getPreprocess());
            formData.append('noise_method', getNoiseMethod());

            try {
                const response = await fetch('/transcribe', {
                    method: 'POST',
                    body: formData
                });
                const requestId = response.headers.get('X-Request-ID') || 'N/A';
                const text = await response.text();

                if (response.ok) {
                    setStatus('success', `Success! Request ID: ${requestId}`);
                    setResult(text);
                } else {
                    setStatus('error', `Error (${response.status}). Request ID: ${requestId}`);
                    setResult(text);
                }
            } catch (e) {
                setStatus('error', `Network error: ${e.message}`);
                setResult(e.toString());
            }
        }

        async function submitMediaUrl() {
            const url = document.getElementById('media_url').value.trim();
            if (!url) {
                setStatus('error', 'Please enter a URL');
                return;
            }

            setStatus('loading', 'Downloading and processing...');
            const requestBody = { media_url: url, output_language: getSelectedLanguage(), asr_model: getSelectedModel(), preprocess: getPreprocess(), noise_method: getNoiseMethod() };

            try {
                const response = await fetch('/transcribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                const requestId = response.headers.get('X-Request-ID') || 'N/A';
                const text = await response.text();

                if (response.ok) {
                    setStatus('success', `Success! Request ID: ${requestId}`);
                    setResult(text);
                } else {
                    setStatus('error', `Error (${response.status}). Request ID: ${requestId}`);
                    setResult(text);
                }
            } catch (e) {
                setStatus('error', `Network error: ${e.message}`);
                setResult(e.toString());
            }
        }

        async function submitYoutube() {
            const url = document.getElementById('youtube_url').value.trim();
            if (!url) {
                setStatus('error', 'Please enter a YouTube URL');
                return;
            }

            setStatus('loading', 'Downloading YouTube audio and processing...');
            const requestBody = { youtube_url: url, output_language: getSelectedLanguage(), asr_model: getSelectedModel(), preprocess: getPreprocess(), noise_method: getNoiseMethod() };

            try {
                const response = await fetch('/transcribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                const requestId = response.headers.get('X-Request-ID') || 'N/A';
                const text = await response.text();

                if (response.ok) {
                    setStatus('success', `Success! Request ID: ${requestId}`);
                    setResult(text);
                } else {
                    setStatus('error', `Error (${response.status}). Request ID: ${requestId}`);
                    setResult(text);
                }
            } catch (e) {
                setStatus('error', `Network error: ${e.message}`);
                setResult(e.toString());
            }
        }

        async function runParallelRequests() {
            setStatus('loading', 'Running 2 parallel requests...');

            // Create two simple JSON requests
            const makeRequest = async (id) => {
                const start = Date.now();
                try {
                    const response = await fetch('/transcribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({})  // Empty request to trigger validation error - quick test
                    });
                    const requestId = response.headers.get('X-Request-ID') || 'N/A';
                    const text = await response.text();
                    const elapsed = Date.now() - start;
                    return `Request ${id}: Status ${response.status}, Request-ID: ${requestId}, Time: ${elapsed}ms\\n${text}`;
                } catch (e) {
                    return `Request ${id}: Error - ${e.message}`;
                }
            };

            try {
                const results = await Promise.all([
                    makeRequest(1),
                    makeRequest(2)
                ]);

                setStatus('success', 'Parallel requests completed!');
                setResult(results.join('\\n\\n---\\n\\n'));
            } catch (e) {
                setStatus('error', `Error: ${e.message}`);
                setResult(e.toString());
            }
        }

        async function checkHealth() {
            setStatus('loading', 'Checking health...');

            try {
                const response = await fetch('/health');
                const data = await response.json();
                setStatus('success', 'Health check passed!');
                setResult(JSON.stringify(data, null, 2));
            } catch (e) {
                setStatus('error', `Health check failed: ${e.message}`);
                setResult(e.toString());
            }
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the HTML test page."""
    return HTML_PAGE


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(
    request: Request,
    file: Optional[UploadFile] = File(None),
):
    """
    Transcribe audio/video to plain text.

    Accepts:
    - JSON body with youtube_url or media_url (and optional output_language)
    - Multipart form with file upload (and optional output_language field)

    Returns plain text transcription.
    """
    # Generate unique request ID for concurrency safety
    request_id = str(uuid.uuid4())
    logger = get_request_logger(request_id)
    temp_dir: Optional[Path] = None

    try:
        # Create per-request temp directory
        temp_dir = create_request_temp_dir(request_id)
        logger.info(f"Created temp directory: {temp_dir}")

        # Determine input type
        input_path: Optional[Path] = None
        output_language: Optional[str] = None  # None means auto-detect
        asr_model: str = "whisper"  # Default to Whisper
        preprocess: bool = False  # Audio preprocessing toggle
        noise_method: str = "noisereduce"  # Noise reduction method

        # Check if it's a file upload (multipart form)
        content_type = request.headers.get("content-type", "")
        if file and file.filename:
            logger.info(f"Processing file upload: {file.filename}")
            input_path = await ingest_upload(file, request_id, temp_dir)

            # Try to get output_language and asr_model from form data
            if "multipart/form-data" in content_type:
                form = await request.form()
                form_lang = form.get("output_language")
                form_model = form.get("asr_model")
                # "auto" or None means auto-detect, otherwise use the specified language
                if form_lang and form_lang != "auto":
                    output_language = form_lang
                # Get ASR model selection
                if form_model:
                    asr_model = form_model
                # Get preprocess toggle
                form_preprocess = form.get("preprocess")
                if form_preprocess and form_preprocess.lower() == "true":
                    preprocess = True
                # Get noise method
                form_noise = form.get("noise_method")
                if form_noise and form_noise in ("noisereduce", "deepfilternet"):
                    noise_method = form_noise

        # Check if it's a JSON request
        elif "application/json" in content_type:
            try:
                body = await request.json()
                req = TranscribeRequest(**body)
            except Exception as e:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid JSON", "detail": str(e)},
                    headers={"X-Request-ID": request_id}
                )

            # Get output language from request ("auto" or None means auto-detect)
            if req.output_language and req.output_language != "auto":
                output_language = req.output_language

            # Get ASR model selection
            if req.asr_model:
                asr_model = req.asr_model

            # Get preprocess toggle
            if req.preprocess:
                preprocess = True

            # Get noise method
            if req.noise_method and req.noise_method in ("noisereduce", "deepfilternet"):
                noise_method = req.noise_method

            if req.youtube_url:
                logger.info(f"Processing YouTube URL: {req.youtube_url}")
                input_path = await ingest_youtube(req.youtube_url, request_id, temp_dir)

            elif req.media_url:
                logger.info(f"Processing media URL: {req.media_url}")
                input_path = await ingest_media_url(req.media_url, request_id, temp_dir)

        logger.info(f"ASR model: {asr_model}, Output language: {output_language or 'auto-detect'}, Preprocess: {preprocess}, Noise: {noise_method}")

        # Validate we have input
        if input_path is None:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "No input provided",
                    "detail": "Provide youtube_url, media_url (JSON), or file (multipart)"
                },
                headers={"X-Request-ID": request_id}
            )

        # Normalize audio with FFmpeg
        logger.info("Normalizing audio...")
        normalized_path = await normalize_audio(input_path, request_id, temp_dir)

        # Audio preprocessing (if enabled)
        if preprocess:
            logger.info("Audio preprocessing enabled, running pipeline...")
            normalized_path = await preprocess_audio(normalized_path, request_id, temp_dir, noise_method)

        # Transcribe with selected ASR model
        logger.info(f"Starting transcription with {asr_model}...")
        if asr_model == "pingala":
            transcript = await transcribe_audio_pingala(normalized_path, request_id, output_language)
        elif asr_model == "ai4bharat":
            transcript = await transcribe_audio_ai4bharat(normalized_path, request_id, output_language)
        else:
            transcript = await transcribe_audio(normalized_path, request_id, output_language, use_chunking=preprocess)

        logger.info("Transcription complete")
        return PlainTextResponse(
            content=transcript,
            headers={"X-Request-ID": request_id}
        )

    except IngestError as e:
        logger.error(f"Ingestion error: {e}")
        return JSONResponse(
            status_code=400,
            content={"error": "Ingestion failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except PreprocessError as e:
        logger.error(f"Preprocessing error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Audio preprocessing failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except FFmpegError as e:
        logger.error(f"FFmpeg error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Audio processing failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except ASRError as e:
        logger.error(f"ASR error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Transcription failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except PingalaASRError as e:
        logger.error(f"Pingala ASR error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Pingala transcription failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except AI4BharatASRError as e:
        logger.error(f"AI4Bharat ASR error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "AI4Bharat transcription failed", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(e)},
            headers={"X-Request-ID": request_id}
        )

    finally:
        # Always cleanup temp directory
        cleanup_temp_dir(temp_dir, request_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
