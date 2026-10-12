# Diarization Service

Pyannote.audio 4.0 community-1 diarization, isolated in `/opt/pyannote-venv`
to keep torch 2.5.1+cu124 separate from TranscriptionBase main env
(torch 2.1+cu121).

## Endpoints

- `GET /health` → `{"status": "ok", "model": "...", "device": "cuda", "loaded": true}`
- `POST /diarize` → see `app.py` for request/response schema

## Architecture

This service owns ONLY the pyannote pipeline call. All post-processing
(embedding verification, boundary snap, gap fill) stays in
TranscriptionBase main env and runs against the speakers list returned
by this service. See `plan/pyannote_4_migration.md` for the full
architecture rationale.

## Port

`8012` by default. Pre-flight check in `scripts/vast-start.sh` aborts
the launch if it's bound (8011 is vLLM, 8010 is Resemble).

## Run standalone (manual)

```bash
python -m venv /opt/pyannote-venv
/opt/pyannote-venv/bin/pip install -r diarization_service/requirements.txt
HF_AUTH_TOKEN=hf_... \
DIARIZATION_DEVICE=cuda \
/opt/pyannote-venv/bin/uvicorn diarization_service.app:app \
    --host 0.0.0.0 --port 8012
```

In production this is launched by `scripts/vast-start.sh` when
`DIARIZATION_SERVICE_ENABLED=true`.

## HF license gate

The `pyannote/speaker-diarization-community-1` model is gated. One-time
action: visit https://huggingface.co/pyannote/speaker-diarization-community-1
and accept the license under the HuggingFace account that owns
`HF_AUTH_TOKEN`. Without acceptance, the pipeline load 401s.

## Smoke test

```bash
# Health
curl http://localhost:8012/health

# Diarize
curl -X POST http://localhost:8012/diarize \
    -H "Content-Type: application/json" \
    -d '{"audio_path": "/path/to/mono.wav", "num_speakers": 0,
         "min_speakers": 2, "max_speakers": 10}'
```
