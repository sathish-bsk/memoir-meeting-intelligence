#!/bin/bash
mkdir -p /tmp/memoir-logs
exec > >(tee -a /tmp/memoir-logs/startup.log) 2>&1
echo "=== Memoir GPU TranscriptionBase Setup ==="
echo "$(date) | GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'none')"

# === HOST SELECTION LESSON (2026-05-27) ===========================================
# When picking a vast.ai instance for Memoir, prefer Tier-1 ISP hosts (Bell Canada,
# US carriers) over European budget hosts (OVH SAS especially).
#
# Concrete numbers from the May 27 2026 session:
#   Frankfurt OVH SAS A4000:  3-4 MB/s sustained from PyTorch CDN → ~50 min bootstrap,
#                             SSH dropped under load, container restarted mid-install
#                             → had to destroy and re-rent (loss: ~50 min + cost).
#   Montréal Bell Canada A4000: 57 MB/s sustained → ~12 min bootstrap, stable SSH.
#
# Why: OVH oversubscribes shared GPU instances; egress bandwidth gets contention.
# Bell/US Tier-1 carriers run their own backbones with predictable per-customer
# caps + direct AWS CloudFront peering (where PyTorch wheels live).
#
# Pre-rent filters: org != "AS16276 OVH SAS", reliability >= 98%, disk space such
# that listing shows either two values (e.g. "40 GB + 200 GB") or single >=80 GB.
# Avoid CN datacenters entirely (PyTorch CDN times out — see
# feedback_avoid_cn_datacenters_for_deploy.md).
#
# Quick smoke test BEFORE bootstrapping (run from local shell on the rented host):
#   curl -s -o /dev/null -w "%{speed_download}\n" --max-time 8 \
#     https://download.pytorch.org/whl/cu124/torch-2.5.1%2Bcu124-cp310-cp310-linux_x86_64.whl
# Treat <10 MB/sec (10485760 bytes/sec) as a destroy-and-re-rent signal.
# =================================================================================

# Bandwidth pre-flight: 5-second probe of PyTorch CDN. Warn loudly (not fatal) if
# the host's external pipe is too slow to finish bootstrap in reasonable time.
echo "--- bandwidth-preflight: probing PyTorch CDN ---"
PYTORCH_PROBE_URL="https://download.pytorch.org/whl/cu124/torch-2.5.1%2Bcu124-cp310-cp310-linux_x86_64.whl"
PROBE_SPEED=$(curl -s -o /dev/null -w "%{speed_download}" --max-time 8 "$PYTORCH_PROBE_URL" 2>/dev/null | awk '{printf "%d", $1}')
PROBE_SPEED="${PROBE_SPEED:-0}"
PROBE_MBPS=$(( PROBE_SPEED / 1048576 ))
if [ "$PROBE_SPEED" -lt 10485760 ] 2>/dev/null && [ "$PROBE_SPEED" -gt 0 ]; then
    echo "[WARN] PyTorch CDN speed: ${PROBE_MBPS} MB/s — below 10 MB/s threshold."
    echo "[WARN] Bootstrap will be slow (and may hit timeouts on big wheels)."
    echo "[WARN] Consider destroying this rental and picking a host on a Tier-1"
    echo "[WARN] ISP (Bell, US carriers) instead of OVH/budget EU. See top-of-file."
elif [ "$PROBE_SPEED" -gt 0 ]; then
    echo "[INFO] PyTorch CDN speed: ${PROBE_MBPS} MB/s (good)"
else
    echo "[WARN] bandwidth probe failed — proceeding anyway"
fi

# If vast.ai's on-start hook installed an old copy of this script at
# /root/vast-start.sh, disable it before it can race against the canonical
# version under /opt/memoir/app/TranscriptionBase/scripts/. Both writing
# to /tmp/memoir-logs/startup.log + same pip resolver at once corrupts the
# venv (witnessed 2026-05-07).
if [ -f /root/vast-start.sh ] && [ "$(realpath "$0")" != "/root/vast-start.sh" ]; then
    echo "WARN: another copy of vast-start.sh exists at /root/vast-start.sh — disabling"
    mv /root/vast-start.sh "/root/vast-start.sh.disabled-$(date +%s)" || true
fi

echo "Python: $(python --version 2>/dev/null || echo 'not yet installed') at $(which python 2>/dev/null || echo '-')"

# -- Preserve operator-set HF_AUTH_TOKEN across env.sh overwrites --
# Without this, every re-run of vast-start.sh clobbers the live HF token
# back to the empty default below (and historically to a dead leaked
# token). Operator then had to manually re-sed the token after every boot.
# Now: capture the existing token from /opt/memoir/env.sh if it's present
# AND non-empty AND not the historic dead-leaked value, then sed it back
# into the new env.sh after the heredoc writes (see below the ENVEOF).
# Sentinel for the historic dead-token check below. Set to a placeholder
# because the previous real value (redacted)
# is the live no-expiry token we still use — the "leaked" classification
# was stale and was causing the preserve logic to reject the valid token
# on every re-run. Confirmed valid via whoami-v2 on May 17 2026.
PRESERVED_HF_TOKEN=""
DEAD_HF_TOKEN_LEAKED="hf_DEADTOKEN_PLACEHOLDER"
if [ -f /opt/memoir/env.sh ]; then
    EXISTING_HF=$(grep "^export HF_AUTH_TOKEN=" /opt/memoir/env.sh | head -1 \
                  | sed 's/^export HF_AUTH_TOKEN=//; s/^"//; s/"$//')
    if [ -n "$EXISTING_HF" ] \
       && [ "$EXISTING_HF" != "$DEAD_HF_TOKEN_LEAKED" ] \
       && [ "$EXISTING_HF" != '""' ]; then
        PRESERVED_HF_TOKEN="$EXISTING_HF"
        echo "[INFO] Preserving HF_AUTH_TOKEN from previous /opt/memoir/env.sh"
    fi
fi

# -- Write env file that persists across SSH sessions --
# Create /opt/memoir on fresh boxes — vast.ai containers don't have this
# dir by default. Without the mkdir the heredoc below fails with "No such
# file or directory", env never gets sourced, and the script bombs at the
# DIARIZATION_DEVICE=cuda check ~330 lines later. Witnessed on Polish RTX
# 3090 fresh-clone on May 17 2026.
mkdir -p /opt/memoir
ENV_FILE=/opt/memoir/env.sh
# NOTE: values marked REPLACE_ME are secrets and are intentionally not committed.
cat > $ENV_FILE << 'ENVEOF'
# Service-to-service auth (must match EC2 INTERNAL_SERVICE_KEY in
# /etc/secrets/env/*.env). Used by videoclip_common/clients.py for any
# call that goes to core_api or auth_service. Note: the CRM contacts
# call in TranscriptionBase reads the key from job params instead, so
# this env is for OTHER service-to-service paths.
export INTERNAL_SERVICE_KEY="REPLACE_ME"  # secret: fill in before running

# Public base URL for core_api. videoclip_common/config.py defaults to
# http://localhost:8005 which doesn't exist on the GPU instance — without
# this override, common-client calls would silently target a dead URL.
export CORE_API_URL=https://app.meetmemoir.com

# DB (RDS)
export DATABASE_URL="REPLACE_ME"  # secret: fill in before running

# R2 Storage
export STORAGE_BACKEND=s3
export S3_BUCKET=memoir-recordings
export S3_ENDPOINT_URL="REPLACE_ME"  # https://<account-id>.r2.cloudflarestorage.com
export S3_REGION=auto
export AWS_ACCESS_KEY_ID="REPLACE_ME"  # secret: fill in before running
export AWS_SECRET_ACCESS_KEY="REPLACE_ME"  # secret: fill in before running

# Paths
export BOT_RECORDINGS_DIR=/data/recordings
export DATA_DIR=/data/core_api

# ASR -- GPU mode
export ASR_ENGINE=faster-whisper
export ASR_MODEL_SIZE=medium
export ASR_DEVICE=cuda
export ASR_COMPUTE_TYPE=float16

# Diarization -- GPU mode
export DIARIZATION_DEVICE=cuda
export DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
# HF token — DO NOT commit a real token here. Empty by default so a
# stale/leaked token can't slip in via a re-run of this script. Operator
# must set this via /opt/memoir/env.sh (sed in the token) OR pass
# HF_AUTH_TOKEN as an env var before invoking the script. The PRESERVED
# capture+restore block above keeps a previously-set token across re-runs,
# so this only needs to be done ONCE per box. The validity probe later
# in this script will WARN loudly if the token is empty or invalid.
export HF_AUTH_TOKEN=""

# Disable hf-xet + hf_transfer transfer backends. hf-xet 1.5.0 (pulled in
# transitively by huggingface_hub 1.14) has known futex_wait_queue deadlocks
# on fresh huggingface-cli download invocations — observed on May 12 fresh
# box bring-up: Qwen 7B AWQ download stalled at ~1.4/5.5 GB for 8+ min
# with the cli process sleeping on a mutex and ~0 B/s network throughput.
# Falling back to the stock requests-based downloader (the "slow" path) is
# fully reliable and still hits 40+ MB/s on vast.ai network.
export HF_HUB_DISABLE_XET=1
export HF_HUB_ENABLE_HF_TRANSFER=0

# PyTorch CUDA caching allocator: use expandable segments mode (PyTorch 2.3+).
# Default mode allocates fixed-size blocks that can't be merged → after thousands
# of inferences, fragmented free-cells accumulate but new allocations can't reuse
# them → co-tenant ML services hit OOM despite total "free" memory being
# adequate. Production repro on May 12: vLLM ballooned from 8.6 GB (configured)
# to 14 GB after 3 concurrent intelligence calls, blocking the diarization
# service from loading community-1 (~3 GB) and forcing fallback to in-process
# pyannote 3.1 which then also OOM'd. Restart was the only recovery.
# With expandable_segments, PyTorch maintains one growing segment per stream;
# freed slots are tracked in a unified free-list and any size can re-use them.
# Cost: ~1-2% allocation latency (unmeasurable in practice). For multi-tenant
# GPUs (vLLM + pyannote 4.0 service + main TB env all on one GPU) this is the
# right default. See https://pytorch.org/docs/stable/notes/cuda.html
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Pyannote 4.0 community-1 isolated microservice (May 2026 migration).
# Default "inprocess" keeps current 3.1 behavior. Flip DIARIZATION_PROVIDER=service
# AND set DIARIZATION_SERVICE_ENABLED=true to install pyannote.audio 4.0 INTO
# /opt/vllm-venv (co-located; disk doesn't allow a separate isolated venv on
# 40 GB instances). Launches service on port 8012. See plan/pyannote_4_migration.md.
# Pyannote 4.0 community-1 service is the default after the May 11 migration.
# To roll back to in-process pyannote 3.1 (e.g. for emergency debugging),
# set DIARIZATION_PROVIDER=inprocess on the box's env.sh. The dispatcher
# also auto-falls back to in-process 3.1 if the service is unreachable.
export DIARIZATION_SERVICE_ENABLED=true
export DIARIZATION_PROVIDER=service
export DIARIZATION_SERVICE_URL=http://localhost:8012
export DIARIZATION_MODEL_4=pyannote/speaker-diarization-community-1
# Lazy GPU residency for the diarization service (default true on 16 GB
# instances to free ~3 GB VRAM for the ONNX aligner + Tier 3.2 batched Whisper).
# Flip to false when GPU >= 24 GB — see plan/pyannote_4_migration.md §15.
export DIARIZATION_LAZY_GPU=true
# Granular HTTP timeouts — connect fails fast (service down → fall back to
# in-process 3.1), read is generous (service IS doing the work; don't double
# wall clock by re-running on the slower path). Replaces the old single
# DIARIZATION_SERVICE_TIMEOUT_S which the code no longer reads.
export DIARIZATION_SERVICE_CONNECT_TIMEOUT_S=10
export DIARIZATION_SERVICE_READ_TIMEOUT_S=3600

# Long-segment scan in _verify_segment_speakers (May 11 evening). Detects
# mid-segment speaker changes that pyannote 4.0's longer segments hide
# from start/end-only verification passes. See plan §15.4-bis.
# Defaults below are calibrated on the May 11 production smoke test;
# tune per-probe debug logging on (`DIARIZATION_LONG_SEG_DEBUG=true`) for
# first deployments + flip off once thresholds are validated.
export DIARIZATION_LONG_SEG_THRESHOLD=8.0
export DIARIZATION_WORD_GAP_MIN=0.10
export DIARIZATION_LONG_SEG_WINDOW=2.0
export DIARIZATION_OUTLIER_STRICT=0.55
export DIARIZATION_REASSIGN_STRICT=0.60
export DIARIZATION_MARGIN_STRICT=0.25
export DIARIZATION_MAX_SPLITS_PER_SEG=2
export DIARIZATION_LONG_SEG_DEBUG=true

# Sliding-window speaker verify: run a second pass that checks the FIRST
# 2 sec of each segment in addition to the existing last-2s tail pass.
# Catches the "first portion mislabeled" pattern that pyannote 4.0 can
# produce when it clusters a short utterance into an adjacent long
# same-cluster segment. Tail-only check (false) catches Whisper rightward-
# extension; head check catches pyannote clustering errors at the start.
# Default true. Set false to fall back to tail-only behavior if it ever
# causes regressions (rollback is an env flip — no code change needed).
export DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL=true

# Voice embedding (ECAPA-TDNN) device — leave unset for auto (CUDA with
# CPU fallback on OOM). Force `cpu` if you want predictability under
# VRAM pressure; force `cuda` to disable the fallback path entirely.
# export VOICE_EMBEDDING_DEVICE=cuda

# Word-level alignment -- GPU mode
export ALIGNMENT_DEVICE=cuda

# Resemble Enhance
export RESEMBLE_ENHANCE_URL=http://localhost:8010

# GPU Override -- force these regardless of job params
export OVERRIDE_MODEL_SIZE=large-v3-turbo
export OVERRIDE_PROCESSING_MODE=path_b
export OVERRIDE_DIARIZATION=true

# Intelligence -- vLLM is the speed_optimization default. To roll back to
# Ollama: flip INTELLIGENCE_PROVIDER=ollama + VLLM_ENABLED=false, then
# `ollama pull qwen2.5:7b-instruct` if the model isn't on disk.
export INTELLIGENCE_ENABLED=true
export INTELLIGENCE_PROVIDER=vllm
export LLM_BASE_URL=http://localhost:11434
export LLM_MODEL=qwen2.5:7b-instruct
export INTELLIGENCE_LLM_MODEL=qwen2.5:7b-instruct

# Tier 5.1.a — vLLM (active/standby with Ollama; VLLM_ENABLED=true also
# triggers vast-start.sh Step 7b which starts vLLM from /opt/vllm-venv and
# stops Ollama. INTELLIGENCE_PROVIDER must match for routing.)
export VLLM_ENABLED=true
export VLLM_BASE_URL=http://localhost:8011/v1
export VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ
export VLLM_MAX_MODEL_LEN=16384
export VLLM_GPU_MEM_UTILIZATION=0.55

# Tier 3.2 -- Batched Whisper inference (faster-whisper BatchedInferencePipeline).
# When true, packs multiple chunks into one GPU forward pass for ~50-70% ASR speedup.
# Trade-off: loses per-chunk language detection (mixed-language calls degrade)
# and inter-chunk initial_prompt context carry-over (~1-3% WER on long calls
# with consistent jargon). Defaults to English when caller didn't pass language.
export WHISPER_BATCHED_MODE=true
export WHISPER_BATCH_SIZE=8
export WHISPER_BATCHED_LANGUAGE=en

# Tier 5.1.b -- concurrent LLM batches (LLMCorrector + PunctuationRefiner).
# ThreadPoolExecutor max_workers. Only beneficial with vLLM (Ollama serializes).
export LLM_BATCH_CONCURRENCY=4

# Tier 5.1.c -- summary map-reduce. When true, run_summary_only chunks long
# transcripts into ~3500-token windows, summarizes each concurrently, then
# reduces. Critical with Tier 3.2 (re-chunking can produce 280+ segments
# whose formatted prompt blows vLLM's max_model_len=16384). NAME MATTERS:
# code reads LLM_SUMMARY_MAPREDUCE — NOT SUMMARY_MAPREDUCE_PARALLEL.
export LLM_SUMMARY_MAPREDUCE=true

# Tier 5.2 -- merge LLM correction + punctuation refinement into a single
# dual-task prompt per batch. Halves Type 5 LLM round-trips. ~20% Type 5
# speedup. Falls back to separate paths on JSON parse failure.
export MERGE_PUNCT_CORRECTION=true

# LangChain Hub + LangSmith tracing (May 17 2026 — Hub-routed 7 prompts +
# per-LLM-call traces). LANGSMITH_WORKSPACE_ID is REQUIRED for lsv2_sk_*
# service keys — without it, POST /runs returns 403 (see
# feedback_langsmith_workspace_id_required.md). LANGCHAIN_API_KEY mirrors
# LANGSMITH_API_KEY for SDK backward-compat. Project is memoir-prod (the
# memoir-test project was deleted May 17). RESPECT_HUB_MODEL stays false
# until cofounder explicitly sets per-prompt model_config in Hub UI —
# otherwise Hub's default ChatOpenAI + gpt-5.5 would route nowhere usable.
export LANGSMITH_ENABLED=true
export LANGSMITH_API_KEY=""  # optional
export LANGCHAIN_API_KEY=$LANGSMITH_API_KEY
export LANGSMITH_PROJECT=memoir-prod
export LANGSMITH_WORKSPACE_ID=""  # optional
export LANGCHAIN_WORKSPACE_ID=$LANGSMITH_WORKSPACE_ID
export LANGCHAIN_ENABLED=true
export LANGCHAIN_HUB_OWNER=memoir-ai
export LANGCHAIN_HUB_TAG=production
export LANGCHAIN_PREWARM_ON_STARTUP=true
export LANGCHAIN_RESPECT_HUB_MODEL=false

# Redis (EC2 -- for progress tracking)
export REDIS_URL="REPLACE_ME"  # secret: fill in before running

# Worker
export TRANSCRIPTION_SERVICE_URL=http://localhost:8000
export POLL_INTERVAL_SECONDS=5
export MAX_CONCURRENT_JOBS=1
export STALE_JOB_TIMEOUT_MINUTES=30
export TRANSCRIPTION_GPU_TYPE=T4

# Force unbuffered Python output so logs flush immediately
export PYTHONUNBUFFERED=1
ENVEOF

# -- Restore preserved HF_AUTH_TOKEN from prior env.sh (if any) --
# Pairs with the capture block at top of script. Operator's manually-set
# token survives every re-run of vast-start.sh from now on.
if [ -n "$PRESERVED_HF_TOKEN" ]; then
    sed -i "s|^export HF_AUTH_TOKEN=.*|export HF_AUTH_TOKEN=$PRESERVED_HF_TOKEN|" $ENV_FILE
    echo "[INFO] Restored HF_AUTH_TOKEN into new env.sh"
elif [ -n "$HF_AUTH_TOKEN" ]; then
    # Fresh-box path: no prior env.sh, but operator passed token via shell
    # env (e.g. `HF_AUTH_TOKEN=hf_xxx bash vast-start.sh`). Honor it so the
    # pyannote pre-download + diarization service launch get a valid token
    # on first boot — no need for a follow-up sed + re-run.
    sed -i "s|^export HF_AUTH_TOKEN=.*|export HF_AUTH_TOKEN=$HF_AUTH_TOKEN|" $ENV_FILE
    echo "[INFO] Seeded HF_AUTH_TOKEN from shell env into new env.sh"
fi

# Load env vars for this session
source $ENV_FILE
mkdir -p /data/recordings /data/core_api
chown -R root:root /data/recordings /data/core_api 2>/dev/null || true
chmod 755 /data/recordings /data/core_api 2>/dev/null || true

# ============================================================================
# Hardware-conditional runtime tuning — auto-detect VRAM + disk and export
# settings that the Python pipeline + later install/launch blocks honor.
# Values appended to env.sh below so they persist for `source /opt/memoir/env.sh`
# in interactive shells and `EnvironmentFile=` consumers.
# ============================================================================

# --- VRAM-gated _free_gpu_models() ---
# Below 20 GB total VRAM, the main TB env's models (Whisper + pyannote +
# voice-embeddings + aligner) won't all fit alongside vLLM/Ollama, so the
# pipeline must free them between stages. At 20 GB+, models stay loaded
# and Type 1/4/5/7 transitions skip the 5-10s model reload penalty.
# Overridable: set MEMOIR_FREE_GPU_MODELS=true|false|auto in env before running.
FREE_GPU_VRAM_THRESHOLD_GB="${MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB:-20}"
TOTAL_VRAM_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
TOTAL_VRAM_MIB="${TOTAL_VRAM_MIB:-0}"
TOTAL_VRAM_GB=$(( TOTAL_VRAM_MIB / 1024 ))
if [ "${MEMOIR_FREE_GPU_MODELS:-auto}" = "true" ] || [ "${MEMOIR_FREE_GPU_MODELS:-auto}" = "false" ]; then
    AUTO_FREE_GPU_MODELS="${MEMOIR_FREE_GPU_MODELS}"
    echo "[INFO] MEMOIR_FREE_GPU_MODELS overridden in env: ${AUTO_FREE_GPU_MODELS}"
elif [ "$TOTAL_VRAM_GB" -ge "$FREE_GPU_VRAM_THRESHOLD_GB" ]; then
    AUTO_FREE_GPU_MODELS="false"
    echo "[INFO] VRAM: ${TOTAL_VRAM_GB} GB >= ${FREE_GPU_VRAM_THRESHOLD_GB} GB threshold → MEMOIR_FREE_GPU_MODELS=false (skip inter-stage frees)"
else
    AUTO_FREE_GPU_MODELS="true"
    echo "[INFO] VRAM: ${TOTAL_VRAM_GB} GB <  ${FREE_GPU_VRAM_THRESHOLD_GB} GB threshold → MEMOIR_FREE_GPU_MODELS=true (free between stages)"
fi
export MEMOIR_FREE_GPU_MODELS="$AUTO_FREE_GPU_MODELS"
export MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB="$FREE_GPU_VRAM_THRESHOLD_GB"

# --- Disk-gated pyannote venv mode ---
# pyannote 4.0 needs ~3 GB of disk on top of the base vLLM venv (~1 GB).
# On 40 GB vast.ai boxes that's tight, so we co-locate into /opt/vllm-venv.
# When the box has plenty of disk (50 GB+ free), give pyannote its own
# venv so vLLM version bumps can't break diarization (and vice-versa).
# Overridable: set PYANNOTE_VENV_MODE=colocated|separate|auto in env before run.
SEPARATE_VENV_THRESHOLD_GB="${PYANNOTE_SEPARATE_VENV_DISK_GB:-50}"
DISK_FREE_KB_ROOT=$(df -P / | awk 'NR==2 {print $4}')
DISK_FREE_GB_ROOT=$((DISK_FREE_KB_ROOT / 1024 / 1024))
if [ "${PYANNOTE_VENV_MODE:-auto}" = "separate" ]; then
    AUTO_PYANNOTE_VENV_MODE="separate"
    echo "[INFO] PYANNOTE_VENV_MODE overridden: separate"
elif [ "${PYANNOTE_VENV_MODE:-auto}" = "colocated" ]; then
    AUTO_PYANNOTE_VENV_MODE="colocated"
    echo "[INFO] PYANNOTE_VENV_MODE overridden: colocated"
elif [ "$DISK_FREE_GB_ROOT" -ge "$SEPARATE_VENV_THRESHOLD_GB" ]; then
    AUTO_PYANNOTE_VENV_MODE="separate"
    echo "[INFO] Disk: ${DISK_FREE_GB_ROOT} GB free >= ${SEPARATE_VENV_THRESHOLD_GB} GB → PYANNOTE_VENV_MODE=separate (/opt/pyannote-venv)"
else
    AUTO_PYANNOTE_VENV_MODE="colocated"
    echo "[INFO] Disk: ${DISK_FREE_GB_ROOT} GB free <  ${SEPARATE_VENV_THRESHOLD_GB} GB → PYANNOTE_VENV_MODE=colocated (/opt/vllm-venv)"
fi
export PYANNOTE_VENV_MODE="$AUTO_PYANNOTE_VENV_MODE"
if [ "$AUTO_PYANNOTE_VENV_MODE" = "separate" ]; then
    export PYANNOTE_VENV_PATH="/opt/pyannote-venv"
else
    export PYANNOTE_VENV_PATH="/opt/vllm-venv"
fi
export PYANNOTE_SEPARATE_VENV_DISK_GB="$SEPARATE_VENV_THRESHOLD_GB"

# Append the resolved values to env.sh so subsequent shells / systemd-style
# EnvironmentFile= consumers pick them up. Use sed-replace pattern to avoid
# appending duplicates across re-runs.
for kv in \
    "MEMOIR_FREE_GPU_MODELS=$MEMOIR_FREE_GPU_MODELS" \
    "MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB=$MEMOIR_FREE_GPU_VRAM_THRESHOLD_GB" \
    "PYANNOTE_VENV_MODE=$PYANNOTE_VENV_MODE" \
    "PYANNOTE_VENV_PATH=$PYANNOTE_VENV_PATH" \
    "PYANNOTE_SEPARATE_VENV_DISK_GB=$PYANNOTE_SEPARATE_VENV_DISK_GB"; do
    key="${kv%%=*}"
    if grep -q "^export ${key}=" "$ENV_FILE"; then
        sed -i "s|^export ${key}=.*|export ${kv}|" "$ENV_FILE"
    else
        echo "export ${kv}" >> "$ENV_FILE"
    fi
done

# Hard prerequisite for the migration headline number: wespeaker embedding
# in Pass 2 + ECAPA in Pass 3 + ctc-forced-aligner all MUST stay on CUDA.
# If DIARIZATION_DEVICE accidentally falls back to "cpu", verification time
# balloons multi-minute and the speedup vanishes. Bail loudly.
if [ "${DIARIZATION_DEVICE:-cpu}" != "cuda" ]; then
    echo "[FATAL] DIARIZATION_DEVICE must be 'cuda' on the GPU box — got '${DIARIZATION_DEVICE:-cpu}'" >&2
    echo "[FATAL] Verification pass on CPU dominates Type 6 wall clock and masks the migration speedup" >&2
    exit 1
fi

# HF token validity probe — warn (don't fail) so cached models can still
# serve, but flag the operator to update the live token before any fresh
# model download (e.g., pyannote community-1 on first boot) fails with 401.
if [ -z "${HF_AUTH_TOKEN}" ]; then
    echo "[WARN] ===========================================================" >&2
    echo "[WARN] HF_AUTH_TOKEN is EMPTY — required for fresh model downloads." >&2
    echo "[WARN] If models are already cached (e.g., from a prior boot), the" >&2
    echo "[WARN] services will start, but ANY fresh download (e.g., pyannote" >&2
    echo "[WARN] community-1 license-gated, Qwen 7B AWQ first-time) will 401." >&2
    echo "[WARN]" >&2
    echo "[WARN] To set the token:" >&2
    echo "[WARN]   1. Get one at https://huggingface.co/settings/tokens" >&2
    echo "[WARN]      (Read scope, account that accepted pyannote license)" >&2
    echo "[WARN]   2. sed -i \"s|^export HF_AUTH_TOKEN=.*|export HF_AUTH_TOKEN=hf_YOURTOKEN|\" /opt/memoir/env.sh" >&2
    echo "[WARN]   3. source /opt/memoir/env.sh" >&2
    echo "[WARN]" >&2
    echo "[WARN] Re-running this script will PRESERVE the token (capture+restore)." >&2
    echo "[WARN] ===========================================================" >&2
else
    WHOAMI=$(curl -s -m 5 -H "Authorization: Bearer ${HF_AUTH_TOKEN}" \
                  https://huggingface.co/api/whoami-v2 2>/dev/null)
    if echo "$WHOAMI" | grep -q '"error"'; then
        echo "[WARN] HF_AUTH_TOKEN is INVALID (whoami-v2 returned error)." >&2
        echo "[WARN] Cached models still work, but new downloads will 401." >&2
        echo "[WARN] Update /opt/memoir/env.sh with a fresh token, then 'source /opt/memoir/env.sh'" >&2
        echo "[WARN] Get a token at https://huggingface.co/settings/tokens (Read scope, account that accepted pyannote license)" >&2
    elif echo "$WHOAMI" | grep -q '"name"'; then
        HF_USER=$(echo "$WHOAMI" | grep -oE '"name":"[^"]*"' | head -1 | cut -d'"' -f4)
        echo "HF token OK (user: $HF_USER)"
    fi
fi

# Move HF cache to /data if it's a separate mount with more headroom
# (40 GB root is tight; /data is often 100+ GB on vast.ai instances). Falls
# back to default ~/.cache/huggingface if /data is on the same filesystem.
ROOT_MNT=$(stat -c %m / 2>/dev/null)
DATA_MNT=$(stat -c %m /data 2>/dev/null)
if [ -n "$DATA_MNT" ] && [ "$DATA_MNT" != "$ROOT_MNT" ] && [ "$DATA_MNT" != "/" ]; then
    echo "Moving HF cache to /data (separate filesystem)"
    mkdir -p /data/hf-cache
    export HF_HOME=/data/hf-cache
    echo "export HF_HOME=/data/hf-cache" >> $ENV_FILE
else
    echo "HF cache stays on root filesystem (/data is on same mount as /)"
fi

# Sanity-check GPU/driver BEFORE pulling 6 GB of CUDA wheels in Step 3.
# If nvidia-smi fails the box is unusable for ML work and we should
# abort early rather than wasting time downloading. Soft-fail with a
# loud warning so CPU-only smoke tests can still proceed if intended.
if ! nvidia-smi --query-gpu=name --format=csv,noheader > /dev/null 2>&1; then
    echo "WARN: nvidia-smi failed — no GPU detected. CUDA wheels will install but"
    echo "      pyannote/whisper/vllm will not run. Continuing anyway (set"
    echo "      MEMOIR_REQUIRE_GPU=1 to abort here instead)."
    if [ "${MEMOIR_REQUIRE_GPU:-0}" = "1" ]; then
        echo "FATAL: MEMOIR_REQUIRE_GPU=1 and no GPU. Aborting."
        exit 2
    fi
else
    echo "GPU: $(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)"
fi

# Driver version sanity check — cu124 wheels (vLLM 0.7.3, pyannote 4.0
# community-1) need NVIDIA driver >= 550. cu121 fallback path needs >= 525.
# Older drivers install but fail at runtime with "CUDA driver too old".
# Warn loudly so operator can re-rent before 6 GB of CUDA wheels download.
DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
DRIVER_MAJOR=$(echo "$DRIVER_VERSION" | cut -d. -f1 2>/dev/null)
if [ -n "$DRIVER_MAJOR" ] && [ "$DRIVER_MAJOR" -lt 550 ] 2>/dev/null; then
    echo "[WARN] ===========================================================" >&2
    echo "[WARN] Driver $DRIVER_VERSION is below 550." >&2
    echo "[WARN] cu124 stack (vLLM 0.7.3, pyannote 4.0 community-1) may fail." >&2
    echo "[WARN] Forward-compat usually works (cu124 wheels often run on" >&2
    echo "[WARN] driver 535-549) but this is NOT the tested combo." >&2
    echo "[WARN]" >&2
    echo "[WARN] Recommended: re-rent this vast.ai instance with driver >= 550" >&2
    echo "[WARN] (filter 'driver_version >= 550' on vast.ai search)." >&2
    echo "[WARN]" >&2
    echo "[WARN] Continuing anyway — install will likely succeed but runtime" >&2
    echo "[WARN] may CUDA-error in /diarize or vLLM model load." >&2
    echo "[WARN] ===========================================================" >&2
elif [ -n "$DRIVER_MAJOR" ]; then
    echo "Driver $DRIVER_VERSION (>= 550) — cu124 stack supported."
fi

# Auto-load on SSH login (bashrc for interactive, profile.d for login shells)
grep -q 'source /opt/memoir/env.sh' /root/.bashrc 2>/dev/null || echo 'source /opt/memoir/env.sh' >> /root/.bashrc
ln -sf /opt/memoir/env.sh /etc/profile.d/memoir.sh

# ==================================================================
# STEP 1: System dependencies
# ==================================================================
echo ""
echo "=== Step 1: System deps ==="
LOG_DIR=/tmp/memoir-logs
mkdir -p $LOG_DIR /data/recordings /data/core_api

# Free disk space -- pip/conda caches can fill 20GB+ on 40GB instances
pip cache purge 2>/dev/null
rm -rf /opt/conda/pkgs/* /root/.cache/pip 2>/dev/null

# F6 emergency reclaim — sweep stranded /tmp job scratch from prior runs
# (TB restart killed the in-flight job's inner finally before it could
# rmtree its temp_dir). Each stale dir is ~50-200 MB on a 30-min recording.
echo "Sweeping stale /tmp job scratch..."
find /tmp -maxdepth 1 -type d \( \
    -name 'preprocess_job_*' -o \
    -name 'enhance_job_*' -o \
    -name 'diarize_job_*' -o \
    -name 'asr_job_*' -o \
    -name 'fast_p5_job_*' -o \
    -name 'asr_postprocess_job_*' -o \
    -name 'intelligence_job_*' \
\) -mmin +30 -exec rm -rf {} + 2>/dev/null
find /tmp -maxdepth 1 -name 'tmp*.wav' -mmin +30 -delete 2>/dev/null

# Emergency reclaim if root usage is above 85% — sweep aggressively
ROOT_USED_PCT=$(df / 2>/dev/null | awk 'NR==2 {gsub("%",""); print $5}')
if [ -n "$ROOT_USED_PCT" ] && [ "$ROOT_USED_PCT" -gt 85 ]; then
    echo "WARN: root at ${ROOT_USED_PCT}% — aggressive reclaim"
    # Newer scratch dirs (no age filter) — anything not in flight right now
    find /tmp -maxdepth 1 -type d \( \
        -name 'preprocess_job_*' -o -name 'enhance_job_*' -o \
        -name 'diarize_job_*' -o -name 'asr_job_*' -o \
        -name 'fast_p5_job_*' -o -name 'asr_postprocess_job_*' -o \
        -name 'intelligence_job_*' \
    \) -exec rm -rf {} + 2>/dev/null
    rm -rf /root/.cache/huggingface/hub/tmp* 2>/dev/null
    rm -rf /var/lib/apt/lists/* 2>/dev/null
    apt-get clean 2>/dev/null
fi
echo "Disk: $(df -h / | tail -1 | awk '{print $4}') free"

apt-get update
apt-get install -y ffmpeg libsndfile1 libsndfile1-dev git git-lfs curl ca-certificates build-essential gcc g++ \
    python3-pip python3-dev python3.10-venv zstd
# Ensure python/pip commands exist (some base images only have python3/pip3)
which python > /dev/null 2>&1 || ln -sf /usr/bin/python3 /usr/bin/python
which pip > /dev/null 2>&1 || ln -sf /usr/bin/pip3 /usr/bin/pip
git lfs install

# Verify gcc is installed
if ! which gcc > /dev/null 2>&1; then
    echo "FATAL: gcc not installed"
    exit 1
fi
echo "gcc: $(which gcc) -- $(gcc --version | head -1)"

# Fix conda compiler_compat -- it shadows system gcc with a broken stub
if [ -d /opt/conda/compiler_compat ]; then
    echo "Fixing conda compiler_compat symlinks..."
    ln -sf $(which gcc) /opt/conda/compiler_compat/gcc
    ln -sf $(which g++) /opt/conda/compiler_compat/g++
    ln -sf $(which cc) /opt/conda/compiler_compat/cc
    ln -sf $(which c++) /opt/conda/compiler_compat/c++
fi

# ==================================================================
# STEP 2: Clone / update repo
# ==================================================================
echo ""
echo "=== Step 2: Repo ==="
REPO_DIR=/opt/memoir/app
cd $REPO_DIR && git pull || true

# ==================================================================
# STEP 3: Python dependencies (ordered by importance)
# ==================================================================
echo ""
echo "=== Step 3: Python deps ==="

# 3a. Core web framework + DB (app won't start without these)
echo "--- 3a: Core framework ---"
pip install --upgrade pip "setuptools>=68.0" wheel
pip install \
    fastapi uvicorn python-multipart pydantic httpx \
    asyncpg python-dotenv soundfile pyyaml

# 3b. ASR engines
echo "--- 3b: ASR engines ---"
pip install faster-whisper

# 3c. Audio processing
echo "--- 3c: Audio processing ---"
pip install "numpy<2" noisereduce

# 3d. Speaker diarization + ML deps
# pyannote.audio>=3.1 requires lightning which conflicts with huggingface_hub<0.21.
# Fix: install pyannote with --no-deps, then install its deps individually.
echo "--- 3d: Diarization + ML deps ---"
pip install "pyannote.audio==3.1.1" --no-deps
pip install speechbrain deepmultilingualpunctuation
pip install "pytorch-lightning>=2.0.1,<2.2" einops "pyannote.core>=5.0.0" \
    "pyannote.database>=5.0.1" "pyannote.pipeline>=3.0.1" "pyannote.metrics>=3.2" \
    "asteroid-filterbanks>=0.4.0" "torch-audiomentations>=0.11.0" \
    "pytorch-metric-learning>=2.1" "omegaconf>=2.1,<3.0" "semver>=3.0.0" \
    "tensorboardX>=2.6" "torchmetrics>=0.11.0"
pip install "numpy<2"
pip install "huggingface_hub>=0.20,<0.21" "transformers>=4.36,<4.41" "tokenizers>=0.19,<0.20"

# Purge pip cache after the biggest install (3d is ~3 GB) to reclaim disk
# before subsequent sub-steps. On 40 GB instances every GB saved here
# reduces the risk of mid-install ENOSPC during 3h-vllm.
pip cache purge 2>/dev/null && echo "  pip cache purged (post-3d)"

# 3e. GPU alignment (onnxruntime with CUDA 12 + cuDNN 8)
# PyPI builds need cuDNN 9 (driver >= 560). Microsoft's CUDA-12 index has cuDNN 8 build.
echo "--- 3e: GPU alignment runtime ---"
pip install onnxruntime-gpu==1.18.0 --extra-index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/

# 3f. Word-level alignment (OPTIONAL -- needs C++ compilation)
echo "--- 3f: Word alignment (optional) ---"
pip install ctc-forced-aligner unidecode || echo "WARN: ctc-forced-aligner failed to build -- word alignment disabled (non-fatal)"

# 3g. Vocabulary correction + Redis
echo "--- 3g: Vocabulary correction + Redis ---"
pip install jellyfish rapidfuzz "redis>=5.0.0"

# 3h. Intelligence pipeline providers
echo "--- 3h: Intelligence providers ---"
pip install anthropic groq google-genai

# 3h-vllm. Optional vLLM (Tier 5.1.a) — install only when explicitly enabled.
# Installed into ISOLATED venv at /opt/vllm-venv so its torch stack doesn't
# conflict with the main env's torch 2.1.0+cu121 / pyannote 3.1 pin.
# TranscriptionBase reaches vLLM only via HTTP on :8011 — no shared Python state.
#
# Version pins (verified May 11 on vast.ai driver 550 / CUDA 12.4):
#   - vllm==0.7.3 — last 0.7.x release; 0.8+ ships cu126+, 0.20+ ships cu130+
#     which require driver 555+ (we have 550 → "NVIDIA driver too old")
#   - torch 2.5.1+cu124 — vllm 0.7.x's required range, matches driver
#   - transformers >=4.45,<4.51 — vllm 0.7.3 expects all_special_tokens_extended
#     attribute on tokenizers, removed in transformers 4.51+
if [ "${VLLM_ENABLED:-false}" = "true" ]; then
    echo "--- 3h-vllm: vLLM (opt-in, isolated venv) ---"
    # Regenerate venv if missing OR if the venv's python is broken (partial
    # install from a previous run). Treat /opt/vllm-venv/bin/python as the
    # canonical health signal — pip + vllm both depend on it working.
    if [ ! -x /opt/vllm-venv/bin/python ] || ! /opt/vllm-venv/bin/python -c "" 2>/dev/null; then
        echo "Creating fresh /opt/vllm-venv..."
        rm -rf /opt/vllm-venv 2>/dev/null
        python3.10 -m venv /opt/vllm-venv
        /opt/vllm-venv/bin/pip install --upgrade pip wheel setuptools >/dev/null 2>&1
    fi

    # Pin torch FIRST so vllm picks up the right CUDA-12.4 wheels instead
    # of pulling cu128+ defaults.
    /opt/vllm-venv/bin/pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 \
        --index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -3
    /opt/vllm-venv/bin/pip install "vllm==0.7.3" \
        "transformers>=4.45,<4.51" "tokenizers>=0.20,<0.22" 2>&1 | tail -3 \
        || echo "WARN: vLLM install failed -- vLLM provider will be unavailable"

    # Verify vllm imports cleanly (catches transformers-version mismatch
    # + torch ABI issues that surface only at import time).
    if /opt/vllm-venv/bin/python -c "import vllm" 2>/dev/null; then
        echo "vLLM import OK ($(/opt/vllm-venv/bin/python -c 'import vllm; print(vllm.__version__)'))"
    else
        echo "WARN: vllm Python import failed — Step 7b will skip vLLM startup"
    fi

    # Purge cache to claw back ~2 GB before model preload + service start
    /opt/vllm-venv/bin/pip cache purge 2>/dev/null && echo "  vllm-venv pip cache purged"
fi

# 3h-pyannote. Optional pyannote 4.0 community-1 (F4 migration) — install only
# when explicitly enabled. ARCHITECTURAL CHANGE FROM ORIGINAL PLAN:
#   Verified May 11 that a vast.ai 40 GB instance with vLLM + Resemble + TB
#   main env can't host an additional 3.4 GB /opt/pyannote-venv (disk full at
#   "scipy/optimize/tests/" partway through the install). The clean isolated-
#   venv design from the plan doesn't fit on the disk footprint we ship to.
#
#   Pivot: co-locate pyannote.audio INTO /opt/vllm-venv. Both need torch 2.5.1+cu124
#   — vllm 0.7.3 already installs it. We add only ~400 MB of pyannote deps on top.
#   The "isolation from TB main env" goal of the original plan is preserved
#   (TB main env still has its torch 2.1+cu121 / pyannote 3.1 stack, unchanged).
#   The plan-isolation between pyannote 4.0 and vllm is sacrificed but they
#   already share the same torch+CUDA stack so there was nothing to isolate.
#
# Install sequence (verified May 11 on A4000 / driver 550 / 5 GB free):
#   1. Skip if VLLM_ENABLED=false (no venv to co-locate into)
#   2. Disk pre-flight: need ≥1 GB free for pyannote deps
#   3. pyannote.audio --no-deps (resolver otherwise upgrades torch to 2.11+)
#   4. Manual install of pyannote's real deps with a torch constraint file
#      preventing any of them from triggering a torch upgrade
#   5. Pre-warm community-1 model so first /diarize is fast
#
# WHY torch 2.5.1+cu124 despite pyannote.audio metadata saying torch>=2.8:
#   The metadata pin is paranoia. Runtime test (smoke test on 30 s pyannote
#   sample.wav, 0.89 s on A4000, 2 speakers detected correctly) confirms
#   4.0.4 + community-1 works fine on torch 2.5.1. Using cu124 keeps us on
#   driver 550 (no driver-560 cliff).
if [ "${DIARIZATION_SERVICE_ENABLED:-false}" = "true" ]; then
    echo "--- 3h-pyannote: pyannote 4.0 service (venv mode: ${PYANNOTE_VENV_MODE}, path: ${PYANNOTE_VENV_PATH}) ---"

    # Validate venv selection — only colocated needs VLLM_ENABLED (it reuses
    # /opt/vllm-venv). Separate mode bootstraps its own torch + pyannote at
    # /opt/pyannote-venv, no dependency on vLLM.
    SKIP_PYANNOTE_INSTALL=false
    if [ "${PYANNOTE_VENV_MODE}" = "colocated" ]; then
        if [ "${VLLM_ENABLED:-false}" != "true" ]; then
            echo "WARN: PYANNOTE_VENV_MODE=colocated requires VLLM_ENABLED=true (shared venv)."
            echo "       Skipping pyannote install. Diarization service will be unavailable."
            echo "       To run pyannote without vLLM, set PYANNOTE_VENV_MODE=separate."
            SKIP_PYANNOTE_INSTALL=true
        elif [ ! -x /opt/vllm-venv/bin/python ]; then
            echo "WARN: /opt/vllm-venv missing — Step 3h-vllm must complete first. Skipping pyannote install."
            SKIP_PYANNOTE_INSTALL=true
        fi
    else
        # Separate mode — bootstrap /opt/pyannote-venv if missing or corrupted.
        if [ ! -x /opt/pyannote-venv/bin/python ] || ! /opt/pyannote-venv/bin/python -c "" 2>/dev/null; then
            echo "Creating fresh /opt/pyannote-venv..."
            rm -rf /opt/pyannote-venv 2>/dev/null
            python3.10 -m venv /opt/pyannote-venv
            /opt/pyannote-venv/bin/pip install --upgrade pip wheel setuptools >/dev/null 2>&1
            echo "Installing torch 2.5.1 + cu124 into /opt/pyannote-venv..."
            /opt/pyannote-venv/bin/pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
                --index-url https://download.pytorch.org/whl/cu124 2>&1 | tail -2 \
                || echo "WARN: torch install into /opt/pyannote-venv failed"
            /opt/pyannote-venv/bin/pip install --no-cache-dir uvicorn fastapi 2>&1 | tail -2
        fi
    fi

    if [ "$SKIP_PYANNOTE_INSTALL" = "false" ]; then
        # Disk pre-flight — need ≥1 GB free for pyannote deps (~400-500 MB actual)
        DISK_FREE_KB=$(df -P / | awk 'NR==2 {print $4}')
        DISK_FREE_GB=$((DISK_FREE_KB / 1024 / 1024))
        if [ "$DISK_FREE_GB" -lt 1 ]; then
            echo "WARN: Only ${DISK_FREE_GB} GB free on / — pyannote install needs ≥1 GB. Skipping."
        else
            echo "Disk pre-flight: ${DISK_FREE_GB} GB free on / (need 1 GB)"

            # Step 1: pyannote.audio --no-deps (resolver pulls cu13 torch otherwise)
            "${PYANNOTE_VENV_PATH}/bin/pip" install --no-cache-dir --no-deps "pyannote.audio==4.0.4" 2>&1 | tail -2

            # Step 2: pyannote's real deps + soundfile/torchaudio under torch+numpy
            # constraint so none of them trigger an upgrade. numpy<2.0 keeps us
            # consistent with vLLM's pin in colocated mode (vllm 0.7.3 requires
            # numpy<2.0). Same pin works fine standalone in separate mode.
            cat > /tmp/pyannote-constraints.txt <<EOF
torch==2.5.1
torchaudio==2.5.1
numpy>=1.24,<2.0
EOF
            "${PYANNOTE_VENV_PATH}/bin/pip" install --no-cache-dir \
                -c /tmp/pyannote-constraints.txt \
                lightning \
                pyannote.core pyannote.database pyannote.pipeline \
                speechbrain \
                asteroid-filterbanks torch-audiomentations pytorch-metric-learning \
                omegaconf semver tensorboardX torchmetrics julius \
                "pyannote.metrics<4.0" \
                "opentelemetry-api" "opentelemetry-sdk" \
                "opentelemetry-instrumentation" \
                "opentelemetry-exporter-otlp>=1.34.0" \
                soundfile 2>&1 | tail -3 \
                || echo "WARN: pyannote dep install failed — diarization service will be unavailable"

            # NOTE on pyannote.metrics: pyannote.audio 4.0.4 metadata pins
            # pyannote.metrics>=4.0, but 4.0+ requires numpy>=2 which conflicts
            # with vllm 0.7.3's numpy<2.0 pin. The earlier claim ("metrics not
            # used at runtime — skip it") was WRONG — pyannote.audio.pipelines
            # .multilabel imports pyannote.metrics.base at module load time, so
            # Pipeline.from_pretrained crashes without it (May 12 production
            # repro). Solution: pin to pyannote.metrics<4.0 (3.2.1) — the older
            # version doesn't need numpy>=2 and exposes the same BaseMetric API.
            # NOTE on opentelemetry: pyannote.audio.telemetry imports
            # opentelemetry.metrics + opentelemetry.exporter.otlp.proto.http
            # at module load time. Without these, Pipeline.from_pretrained
            # fails with ModuleNotFoundError. Confirmed required at runtime
            # (May 12 production repro).

            # Verify pyannote imports cleanly post-install (catches version-skew).
            # In colocated mode also verify vllm coexists; in separate mode skip the
            # vllm probe (it's not installed there).
            if [ "${PYANNOTE_VENV_MODE}" = "colocated" ]; then
                "${PYANNOTE_VENV_PATH}/bin/python" -c "
import warnings; warnings.filterwarnings('ignore')
from pyannote.audio import Pipeline
import vllm
print('OK: pyannote.audio + vllm coexist in ${PYANNOTE_VENV_PATH}')
" 2>&1 | tail -2 || echo "WARN: post-install import check failed — service will fail at /diarize"
            else
                "${PYANNOTE_VENV_PATH}/bin/python" -c "
import warnings; warnings.filterwarnings('ignore')
from pyannote.audio import Pipeline
print('OK: pyannote.audio in ${PYANNOTE_VENV_PATH} (separate venv)')
" 2>&1 | tail -2 || echo "WARN: post-install import check failed — service will fail at /diarize"
            fi

            # Pre-warm: download community-1 weights so first /diarize request is fast.
            # HF gated — requires license acceptance on the HF account behind HF_AUTH_TOKEN.
            if [ -n "${HF_AUTH_TOKEN}" ]; then
                echo "Pre-downloading pyannote/speaker-diarization-community-1 ..."
                HF_TOKEN="${HF_AUTH_TOKEN}" "${PYANNOTE_VENV_PATH}/bin/python" -c "
import warnings; warnings.filterwarnings('ignore')
from pyannote.audio import Pipeline
Pipeline.from_pretrained('${DIARIZATION_MODEL_4:-pyannote/speaker-diarization-community-1}', token='${HF_AUTH_TOKEN}')
print('community-1 model cached')
" 2>&1 | tail -2 || echo "WARN: community-1 pre-download failed — check HF license acceptance"
            fi

            "${PYANNOTE_VENV_PATH}/bin/pip" cache purge 2>/dev/null
            rm -f /tmp/pyannote-constraints.txt
        fi
    fi
fi

# 3i. Other TranscriptionBase deps (catch anything missed)
# Filter out packages we already pinned (torch, pyannote, huggingface_hub)
# to prevent requirements.txt from overriding our versions
echo "--- 3i: Remaining TranscriptionBase deps ---"
grep -v -E 'videoclip-common|pyannote|huggingface_hub|^torch|openai-whisper|deepfilternet|pingala|transformers|tokenizers|deepmultilingualpunctuation' TranscriptionBase/requirements.txt > /tmp/tb_reqs.txt
pip install -r /tmp/tb_reqs.txt || echo "WARN: Some optional deps failed (non-fatal)"

# 3j. videoclip-common (shared storage library)
echo "--- 3j: videoclip-common ---"
pip install videoclip_common/

# 3k. Transcription worker deps
echo "--- 3k: Worker deps ---"
pip install -r transcription_worker/requirements.txt

# 3l. Resemble Enhance -- isolated venv (has its own torch version)
echo "--- 3l: Resemble Enhance (isolated venv) ---"
if [ ! -d $REPO_DIR/resemble_enhance/venv ]; then
    python -m venv $REPO_DIR/resemble_enhance/venv
fi
$REPO_DIR/resemble_enhance/venv/bin/pip install --upgrade pip
$REPO_DIR/resemble_enhance/venv/bin/pip install -r $REPO_DIR/resemble_enhance/requirements.txt || echo "WARN: resemble-enhance deps failed"

# Fix resemble-enhance CUDA device mismatch bug:
# inference_chunk() moves hwav to CPU but abs_max stays on CUDA -> RuntimeError
RE_INF=$REPO_DIR/resemble_enhance/venv/lib/python3.10/site-packages/resemble_enhance/inference.py
if [ -f "$RE_INF" ]; then
    sed -i 's/hwav = hwav \* abs_max  # Unnormalize/hwav = hwav * abs_max.cpu()  # Unnormalize (ensure same device)/' "$RE_INF"
    echo "  Patched resemble-enhance inference.py (CUDA device fix)"
fi

# 3-final. Fix torch version -- MUST run AFTER all other pip installs
# pyannote.audio 3.x references torchaudio.AudioMetaData (removed in torchaudio 2.2+)
# Various deps (requirements.txt, speechbrain, deepmultilingualpunctuation) may pull
# in newer torch, overwriting the fix. So we pin torch+torchaudio 2.1.0 as the LAST step.
echo "--- 3-final: Pinning torch 2.1.0+cu121 (pyannote compatibility) ---"
pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cu121

# Re-pin versions that other deps may have overridden
pip install "huggingface_hub>=0.20,<0.21" "transformers>=4.36,<4.41" "tokenizers>=0.19,<0.20"

# ==================================================================
# STEP 4: Verify critical imports
# ==================================================================
echo ""
echo "=== Step 4: Verification ==="
FAIL=0
for mod in fastapi uvicorn multipart pydantic httpx asyncpg soundfile faster_whisper numpy torch torchaudio pyannote.audio speechbrain; do
    if python -c "import $mod" 2>/dev/null; then
        echo "  OK: $mod"
    else
        echo "  FAIL: $mod"
        FAIL=1
    fi
done

# Optional modules (warn but don't block)
for mod in ctc_forced_aligner jellyfish rapidfuzz anthropic groq deepmultilingualpunctuation; do
    if python -c "import $mod" 2>/dev/null; then
        echo "  OK: $mod"
    else
        echo "  WARN: $mod (optional)"
    fi
done

if [ "$FAIL" -eq 1 ]; then
    echo "FATAL: Critical imports failed. Aborting."
    exit 1
fi

# Verify CUDA + torch version
echo "  CUDA: $(python -c 'import torch; print(torch.cuda.is_available())' 2>/dev/null)"
echo "  Torch: $(python -c 'import torch; print(torch.__version__)' 2>/dev/null)"
echo "  HF Hub: $(python -c 'import huggingface_hub; print(huggingface_hub.__version__)' 2>/dev/null)"
echo "All critical imports OK"

# ==================================================================
# STEP 4b: Ensure punctuation model deps installed
# ==================================================================
echo ""
echo "=== Step 4b: Punctuation model ==="
# deepmultilingualpunctuation may fail in step 3d due to pyannote conflict.
# Install separately with --no-deps if needed.
python -c "import deepmultilingualpunctuation" 2>/dev/null || {
    echo "Installing deepmultilingualpunctuation..."
    pip install deepmultilingualpunctuation --no-deps 2>&1 | tail -3
}
python -c "from deepmultilingualpunctuation import PunctuationModel; print('Punctuation model OK')" || echo "WARN: deepmultilingualpunctuation not available"

# ==================================================================
# STEP 4c: ctc-forced-aligner ONNX model — pre-download + integrity check
# ==================================================================
# The aligner model (~1.18 GB) is downloaded lazily on first Type 5
# job by ensure_onnx_model() in the ctc_forced_aligner package. If
# disk fills during a job, the download truncates and onnxruntime
# fails to load it (INVALID_PROTOBUF) — validated 2026-05-12 in prod
# after /tmp filled mid-preprocess. Pre-downloading at startup with
# a size check catches truncation before any job runs.
echo ""
echo "=== Step 4c: ctc-forced-aligner ONNX model ==="
ALIGNER_PATH=/root/ctc_forced_aligner/model.onnx
ALIGNER_URL='https://huggingface.co/deskpai/ctc_forced_aligner/resolve/main/04ac86b67129634da93aea76e0147ef3.onnx'
EXPECTED_SIZE=1262421764
mkdir -p /root/ctc_forced_aligner
if [ -f "$ALIGNER_PATH" ]; then
    ACTUAL=$(stat -c%s "$ALIGNER_PATH" 2>/dev/null || echo 0)
    if [ "$ACTUAL" != "$EXPECTED_SIZE" ]; then
        echo "[WARN] aligner model wrong size ($ACTUAL vs $EXPECTED_SIZE bytes) — re-downloading"
        rm -f "$ALIGNER_PATH"
    fi
fi
if [ ! -f "$ALIGNER_PATH" ]; then
    echo "Downloading ctc-aligner model (~1.18 GB)..."
    curl -fL --progress-bar -o "$ALIGNER_PATH" "$ALIGNER_URL" || {
        echo "[FATAL] ctc-aligner download failed — Type 5 alignment will not work"
        exit 1
    }
fi
ACTUAL=$(stat -c%s "$ALIGNER_PATH" 2>/dev/null || echo 0)
if [ "$ACTUAL" != "$EXPECTED_SIZE" ]; then
    echo "[FATAL] aligner model size mismatch after download ($ACTUAL vs $EXPECTED_SIZE bytes)"
    exit 1
fi
echo "ctc-aligner model OK ($ACTUAL bytes)"

# ==================================================================
# STEP 5: Create restart.sh (early -- so it exists even if later steps fail)
# ==================================================================
cat > /opt/memoir/restart.sh << 'RESTARTEOF'
#!/bin/bash
source /opt/memoir/env.sh
LOG_DIR=/tmp/memoir-logs
REPO_DIR=/opt/memoir/app
mkdir -p $LOG_DIR

echo "Stopping services..."
pkill -f 'uvicorn app.main:app.*8000' 2>/dev/null
pkill -f 'uvicorn app:app.*8010' 2>/dev/null
pkill -f 'python -m app.main' 2>/dev/null
sleep 2
# Force kill any remaining processes on ports 8000/8010
fuser -k 8000/tcp 2>/dev/null
fuser -k 8010/tcp 2>/dev/null
# Wait until port 8000 is free
for i in $(seq 1 10); do
    ! fuser 8000/tcp >/dev/null 2>&1 && break
    echo "Waiting for port 8000 to free..."
    sleep 1
done

# Active/standby provider start: exactly one of Ollama/vLLM serves at a time.
# INTELLIGENCE_PROVIDER routes all 6 LLM call sites to a single endpoint.
if [ "${VLLM_ENABLED:-false}" = "true" ]; then
    # Stop Ollama if running — vLLM takes the role; weights stay on disk.
    if pgrep -f "ollama serve" >/dev/null 2>&1; then
        echo "Stopping Ollama (vLLM mode)..."
        pkill -f "ollama serve" 2>/dev/null
        sleep 2
    fi
    if ! curl -sf http://localhost:8011/v1/models >/dev/null 2>&1; then
        echo "Starting vLLM on :8011 (isolated venv)..."
        VLLM_MODEL_LOCAL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
        nohup /opt/vllm-venv/bin/python -m vllm.entrypoints.openai.api_server \
            --model "$VLLM_MODEL_LOCAL" \
            --quantization awq \
            --dtype auto \
            --max-model-len "${VLLM_MAX_MODEL_LEN:-16384}" \
            --gpu-memory-utilization "${VLLM_GPU_MEM_UTILIZATION:-0.55}" \
            --port 8011 --host 0.0.0.0 \
            > $LOG_DIR/vllm.log 2>&1 &
        for i in $(seq 1 90); do curl -sf http://localhost:8011/v1/models >/dev/null 2>&1 && break; sleep 1; done
    fi
else
    # Stop vLLM if running — Ollama takes back the role.
    if pgrep -f "vllm.entrypoints" >/dev/null 2>&1; then
        echo "Stopping vLLM (Ollama mode)..."
        pkill -f "vllm.entrypoints" 2>/dev/null
        sleep 3
    fi
    if ! curl -sf http://localhost:11434/v1/models >/dev/null 2>&1; then
        echo "Starting Ollama..."
        ollama serve > $LOG_DIR/ollama.log 2>&1 &
        for i in $(seq 1 15); do curl -sf http://localhost:11434/v1/models >/dev/null 2>&1 && break; sleep 1; done
    fi
fi

echo "Starting Resemble Enhance on :8010..."
cd $REPO_DIR/resemble_enhance
./venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8010 --workers 1 > $LOG_DIR/resemble.log 2>&1 &

# Diarization service (pyannote 4.0 community-1) — opt-in, venv at ${PYANNOTE_VENV_PATH} on :8012
if [ "${DIARIZATION_SERVICE_ENABLED:-false}" = "true" ] && [ -x "${PYANNOTE_VENV_PATH}/bin/uvicorn" ]; then
    if ss -ltn 2>/dev/null | grep -q ':8012 '; then
        echo "WARN: Port 8012 already bound — skipping diarization service launch"
    else
        echo "Starting diarization service on :8012 from ${PYANNOTE_VENV_PATH} (mode=${PYANNOTE_VENV_MODE})..."
        cd $REPO_DIR/TranscriptionBase
        "${PYANNOTE_VENV_PATH}/bin/uvicorn" diarization_service.app:app \
            --host 0.0.0.0 --port 8012 --workers 1 \
            > $LOG_DIR/diarization-svc.log 2>&1 &
    fi
fi

echo "Starting TranscriptionBase on :8000..."
cd $REPO_DIR/TranscriptionBase
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 > $LOG_DIR/transcription-base.log 2>&1 &

echo "Waiting for TranscriptionBase..."
for i in $(seq 1 30); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
    sleep 2
done

echo "Starting worker..."
cd $REPO_DIR/transcription_worker
python -m app.main > $LOG_DIR/worker.log 2>&1 &

sleep 2
echo "=== Health ==="
curl -sf http://localhost:8000/health && echo ""
curl -sf http://localhost:8010/health && echo ""
if [ "${DIARIZATION_SERVICE_ENABLED:-false}" = "true" ]; then
    curl -sf http://localhost:8012/health && echo ""
fi
echo "All services restarted. Logs: tail -f $LOG_DIR/*.log"
RESTARTEOF
chmod +x /opt/memoir/restart.sh
echo "Created /opt/memoir/restart.sh"

# ==================================================================
# STEP 6: Pre-download models (blocking -- services must wait for models)
# ==================================================================
echo ""
echo "=== Step 6: Models ==="

# Download all 3 models in parallel, then wait for all to finish
echo "Pre-downloading Whisper large-v3-turbo..."
python -c "from faster_whisper.utils import download_model; download_model('large-v3-turbo')" 2>/dev/null &
PID_WHISPER=$!

echo "Pre-downloading diarization model..."
python -c "
import os
os.environ['HF_TOKEN'] = os.environ.get('HF_AUTH_TOKEN', '')
from pyannote.audio import Pipeline
Pipeline.from_pretrained('$DIARIZATION_MODEL')
print('Diarization model downloaded')
" 2>/dev/null &
PID_DIARIZE=$!

echo "Pre-downloading punctuation model..."
python -c "from transformers import pipeline; pipeline('ner', 'oliverguhr/fullstop-punctuation-multilang-large', device=-1)" 2>/dev/null &
PID_PUNCT=$!

# Wait for all model downloads before starting services
echo "Waiting for model downloads..."
wait $PID_WHISPER $PID_DIARIZE $PID_PUNCT 2>/dev/null
echo "All models downloaded ($(date +%H:%M:%S))"

# ==================================================================
# STEP 7: Ollama
# ==================================================================
# Always install the binary (~70 MB) so rollback to Ollama is fast.
# Only pull weights + start the daemon when we're actually going to USE
# Ollama (VLLM_ENABLED=false). On vLLM-mode reboots this saves ~5 min
# pull time AND ~4.7 GB disk that Step 7b would just orphan anyway.
echo ""
echo "=== Step 7: Ollama ==="
if ! command -v ollama &>/dev/null; then
    echo "Installing Ollama binary..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
if [ "${VLLM_ENABLED:-false}" = "true" ]; then
    echo "Skipping Ollama daemon + model pull (VLLM_ENABLED=true — active/standby)."
    echo "  To roll back to Ollama: set VLLM_ENABLED=false in /opt/memoir/env.sh,"
    echo "  then 'ollama serve & ; ollama pull qwen2.5:7b-instruct' (~5 min)."
else
    ollama serve > $LOG_DIR/ollama.log 2>&1 &
    OLLAMA_PID=$!
    for i in $(seq 1 30); do
        curl -sf http://localhost:11434/v1/models >/dev/null 2>&1 && break
        sleep 1
    done
    echo "Pulling qwen2.5:7b-instruct..."
    ollama pull qwen2.5:7b-instruct || echo "WARN: Ollama model pull failed"

    # ----------------------------------------------------------------
    # LangChain cross-provider routing — Phase 7a (plan §10.2)
    # ----------------------------------------------------------------
    # Pre-pull every model in LANGCHAIN_AVAILABLE_MODELS that has the
    # "ollama:" prefix. Idempotent: skips models already present in
    # `ollama list`. Failed pulls log a warning but don't abort startup —
    # router falls back to env default at runtime for missing entries.
    if [ -n "${LANGCHAIN_AVAILABLE_MODELS:-}" ]; then
        echo "Processing LANGCHAIN_AVAILABLE_MODELS for Ollama pre-pull..."
        IFS=',' read -ra _LANGCHAIN_MODELS <<< "$LANGCHAIN_AVAILABLE_MODELS"
        for _entry in "${_LANGCHAIN_MODELS[@]}"; do
            _entry="$(echo "$_entry" | xargs)"  # trim whitespace
            case "$_entry" in
                ollama:*)
                    _model_id="${_entry#ollama:}"
                    if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$_model_id"; then
                        echo "  [ollama] already present: $_model_id"
                    else
                        echo "  [ollama] pulling: $_model_id"
                        ollama pull "$_model_id" \
                            || echo "  WARN: pull failed for $_model_id; router will fall back"
                    fi
                    ;;
            esac
        done
    fi
fi

# ==================================================================
# STEP 7b: vLLM (Tier 5.1.a, opt-in via VLLM_ENABLED=true)
# ==================================================================
# Self-hosted OpenAI-compatible runtime serving Qwen2.5-7B-Instruct-AWQ
# on port 8011. Runs from ISOLATED venv at /opt/vllm-venv so its torch 2.5+
# stack does not collide with the main env's torch 2.1 / pyannote 3.1 pin.
# TranscriptionBase reaches vLLM only via HTTP — no Python import boundary
# crossed. Active/standby with Ollama: when vLLM is up and serving,
# INTELLIGENCE_PROVIDER=vllm routes all 6 LLM call sites here, and the
# Ollama daemon should be stopped (frees ~5 GB VRAM for vLLM's KV cache).
# ==================================================================
if [ "${VLLM_ENABLED:-false}" = "true" ]; then
    echo ""
    echo "=== Step 7b: vLLM (isolated venv) ==="
    VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
    VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-16384}"
    VLLM_GPU_MEM_UTILIZATION="${VLLM_GPU_MEM_UTILIZATION:-0.45}"

    # LangChain Phase 7a (plan §10.3): vLLM serves ONE model per instance.
    # If LANGCHAIN_AVAILABLE_MODELS declares a vllm:* entry that differs
    # from VLLM_MODEL, warn loudly — router will fall back on routed calls.
    if [ -n "${LANGCHAIN_AVAILABLE_MODELS:-}" ]; then
        _DECLARED_VLLM=$(echo "$LANGCHAIN_AVAILABLE_MODELS" \
            | tr ',' '\n' \
            | grep '^vllm:' \
            | head -1 \
            | sed 's/^vllm://')
        if [ -n "$_DECLARED_VLLM" ] && [ "$_DECLARED_VLLM" != "$VLLM_MODEL" ]; then
            echo "WARN: LANGCHAIN_AVAILABLE_MODELS declares vllm:$_DECLARED_VLLM"
            echo "      but VLLM_MODEL=$VLLM_MODEL"
            echo "      Router will fall back on Hub-routed vllm:$_DECLARED_VLLM calls."
        fi
    fi

    if /opt/vllm-venv/bin/python -c "import vllm" 2>/dev/null; then
        # Stop Ollama if running — vLLM takes over the LLM serve role.
        # Weights stay on disk under /root/.ollama so rollback is just
        # `ollama serve &` + env flip back to INTELLIGENCE_PROVIDER=ollama.
        if pgrep -f "ollama serve" >/dev/null; then
            echo "Stopping Ollama (vLLM taking over)..."
            pkill -f "ollama serve" 2>/dev/null
            sleep 2
        fi

        # Recover disk before the ~5 GB Qwen model download. The line 556
        # pip cache purge ran AFTER step 3d, but steps 3e-3l + resemble +
        # main torch 2.1 install all rewrite /root/.cache/pip (~3-5 GB by
        # this point). On 40 GB boxes that's the difference between fit
        # and ENOSPC. Both purges are no-ops if cache is already clean.
        echo "  pre-Qwen disk recovery..."
        pip cache purge 2>/dev/null || true
        /opt/vllm-venv/bin/pip cache purge 2>/dev/null || true
        rm -rf /root/.cache/huggingface/hub/tmp* 2>/dev/null || true
        df -h / | tail -1 | sed 's/^/  disk before model dl: /'

        # Pre-download weights via venv to avoid stalling first call
        /opt/vllm-venv/bin/huggingface-cli download "$VLLM_MODEL" >/dev/null 2>&1 || true

        nohup /opt/vllm-venv/bin/python -m vllm.entrypoints.openai.api_server \
            --model "$VLLM_MODEL" \
            --quantization awq \
            --dtype auto \
            --max-model-len "$VLLM_MAX_MODEL_LEN" \
            --gpu-memory-utilization "$VLLM_GPU_MEM_UTILIZATION" \
            --port 8011 \
            --host 0.0.0.0 \
            > $LOG_DIR/vllm.log 2>&1 &
        VLLM_PID=$!
        echo $VLLM_PID > /var/run/vllm.pid 2>/dev/null || true

        echo "Waiting for vLLM (max 180s — first start downloads weights)..."
        for i in $(seq 1 180); do
            if curl -sf http://localhost:8011/v1/models >/dev/null 2>&1; then
                echo "vLLM ready after ${i}s (PID $VLLM_PID)"
                break
            fi
            sleep 1
        done
        if ! curl -sf http://localhost:8011/v1/models >/dev/null 2>&1; then
            echo "WARN: vLLM did not become ready within 180s -- check $LOG_DIR/vllm.log"
        fi
    else
        echo "WARN: vllm not installed in /opt/vllm-venv -- set VLLM_ENABLED=true before next boot OR run: /opt/vllm-venv/bin/pip install vllm"
    fi
fi

# ==================================================================
# STEP 8: Start services
# ==================================================================
echo ""
echo "=== Step 8: Starting services ==="

# Resemble Enhance (isolated venv)
echo "Starting Resemble Enhance on :8010..."
cd $REPO_DIR/resemble_enhance
./venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8010 --workers 1 > $LOG_DIR/resemble.log 2>&1 &
RE_PID=$!

# Diarization service (pyannote 4.0 community-1) — opt-in, venv at ${PYANNOTE_VENV_PATH} on :8012
DIAR_SVC_PID=""
if [ "${DIARIZATION_SERVICE_ENABLED:-false}" = "true" ] && [ -x "${PYANNOTE_VENV_PATH}/bin/uvicorn" ]; then
    if ss -ltn 2>/dev/null | grep -q ':8012 '; then
        echo "WARN: Port 8012 already bound — skipping diarization service launch"
    else
        echo "Starting diarization service on :8012 from ${PYANNOTE_VENV_PATH} (mode=${PYANNOTE_VENV_MODE})..."
        cd $REPO_DIR/TranscriptionBase
        "${PYANNOTE_VENV_PATH}/bin/uvicorn" diarization_service.app:app \
            --host 0.0.0.0 --port 8012 --workers 1 \
            > $LOG_DIR/diarization-svc.log 2>&1 &
        DIAR_SVC_PID=$!
    fi
fi

# TranscriptionBase
echo "Starting TranscriptionBase on :8000..."
cd $REPO_DIR/TranscriptionBase
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 > $LOG_DIR/transcription-base.log 2>&1 &
TB_PID=$!

# Wait for TranscriptionBase to be ready
echo "Waiting for TranscriptionBase..."
for i in $(seq 1 60); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
    sleep 2
done
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "TranscriptionBase ready"
else
    echo "WARN: TranscriptionBase health check failed -- check logs: tail -f $LOG_DIR/transcription-base.log"
fi

# Transcription worker
echo "Starting worker (poll: ${POLL_INTERVAL_SECONDS}s)..."
cd $REPO_DIR/transcription_worker
python -m app.main > $LOG_DIR/worker.log 2>&1 &
WORKER_PID=$!

# ==================================================================
# STEP 8b: Pre-warm models (Tier 5.5 — non-blocking, best-effort)
# ==================================================================
# Loads model weights into GPU memory + does first inference so the
# first real meeting on this fresh instance doesn't pay ~30-60s cold-load
# per model. Failures are silent — first call falls back to cold-load
# behavior as today. Runs in parallel; total cost is max(N), not sum.
# ==================================================================
echo ""
echo "=== Step 8b: Pre-warm models (background) ==="

# Ollama — load qwen2.5:7b-instruct into GPU; --keepalive 60m holds it there.
# Skipped when vLLM is enabled (active/standby — vLLM took over).
(
  if [ "${VLLM_ENABLED:-false}" = "true" ]; then
    echo "[pre-warm] ollama skipped (VLLM_ENABLED=true — active/standby)"
  else
    for i in $(seq 1 30); do
      curl -sf http://localhost:11434/v1/models >/dev/null 2>&1 && break
      sleep 2
    done
    ollama run qwen2.5:7b-instruct --keepalive 60m <<< "warmup" >/dev/null 2>&1 \
      && echo "[pre-warm] ollama qwen2.5:7b-instruct ready ($(date +%H:%M:%S))" \
      || echo "[pre-warm] ollama warmup failed (non-fatal)"
  fi
) > $LOG_DIR/prewarm-ollama.log 2>&1 &
PREWARM_OLLAMA_PID=$!

# vLLM — only fires if a vLLM service is running on :8011 (Tier 5.1.a opt-in)
(
  if curl -sf http://localhost:8011/v1/models >/dev/null 2>&1; then
    VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
    curl -s -X POST http://localhost:8011/v1/completions \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"$VLLM_MODEL\",\"prompt\":\"warmup\",\"max_tokens\":1}" \
      >/dev/null 2>&1 \
      && echo "[pre-warm] vllm $VLLM_MODEL ready ($(date +%H:%M:%S))" \
      || echo "[pre-warm] vllm warmup failed (non-fatal)"
  else
    echo "[pre-warm] vllm not running on :8011 — skipping"
  fi
) > $LOG_DIR/prewarm-vllm.log 2>&1 &
PREWARM_VLLM_PID=$!

# faster-whisper — load model on GPU and run a 100ms inference
(
  python -c "
import time
t0 = time.time()
try:
    from faster_whisper import WhisperModel
    import numpy as np
    m = WhisperModel('large-v3-turbo', device='cuda', compute_type='float16')
    # 100ms of silence → first forward pass primes CUDA kernels
    silent = np.zeros(1600, dtype=np.float32)
    list(m.transcribe(silent, beam_size=1)[0])
    print(f'[pre-warm] faster-whisper ready in {time.time()-t0:.1f}s')
except Exception as e:
    print(f'[pre-warm] faster-whisper warmup failed: {e}')
" 2>&1
) > $LOG_DIR/prewarm-whisper.log 2>&1 &
PREWARM_WHISPER_PID=$!

# pyannote — load diarization pipeline on GPU
(
  python -c "
import os, time
t0 = time.time()
try:
    os.environ['HF_TOKEN'] = os.environ.get('HF_AUTH_TOKEN', '')
    from pyannote.audio import Pipeline
    import torch
    p = Pipeline.from_pretrained('$DIARIZATION_MODEL')
    if torch.cuda.is_available():
        p.to(torch.device('cuda'))
    print(f'[pre-warm] pyannote ready in {time.time()-t0:.1f}s')
except Exception as e:
    print(f'[pre-warm] pyannote warmup failed: {e}')
" 2>&1
) > $LOG_DIR/prewarm-pyannote.log 2>&1 &
PREWARM_PYANNOTE_PID=$!

# ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) — used by Type 5 verification
# pass AND (2026-05-13) by Type 7 voice-print DB lookup for uploaded recordings.
# Cold-load is ~3-5s. Without prewarm the first meeting after a TB restart pays
# that on every voice-DB lookup. Cached as a module-level global afterwards.
(
  python -c "
import time
t0 = time.time()
try:
    from app.audio_preprocess.services.voice_embeddings import _get_classifier
    _get_classifier()
    print(f'[pre-warm] ECAPA-TDNN ready in {time.time()-t0:.1f}s')
except Exception as e:
    print(f'[pre-warm] ECAPA-TDNN warmup failed (non-fatal): {e}')
" 2>&1
) > $LOG_DIR/prewarm-ecapa.log 2>&1 &
PREWARM_ECAPA_PID=$!

echo "Pre-warm jobs launched in background (PIDs: $PREWARM_OLLAMA_PID $PREWARM_VLLM_PID $PREWARM_WHISPER_PID $PREWARM_PYANNOTE_PID $PREWARM_ECAPA_PID)"
echo "Watch progress: tail -f $LOG_DIR/prewarm-*.log"

echo ""
echo "=== All services started ==="
echo "  Ollama PID:            $OLLAMA_PID  -> $LOG_DIR/ollama.log"
echo "  Resemble PID:          $RE_PID  -> $LOG_DIR/resemble.log"
echo "  TranscriptionBase PID: $TB_PID  -> $LOG_DIR/transcription-base.log"
echo "  Worker PID:            $WORKER_PID  -> $LOG_DIR/worker.log"
echo ""
echo "Tail all logs: tail -f $LOG_DIR/*.log"
echo ""
echo "Health checks:"
echo "  TranscriptionBase: curl http://localhost:8000/health"
echo "  Ollama:            curl http://localhost:11434/v1/models"
echo "  Resemble:          curl http://localhost:8010/health"

# ==================================================================
# STEP 9: Keep alive (survives SSH disconnect)
# ==================================================================
echo "Setup complete. Services running in background."
echo "Use /opt/memoir/restart.sh to restart services."
echo "Logs: tail -f $LOG_DIR/*.log"
tail -f /dev/null
