"""
Configuration for the audio preprocessing pipeline.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class PreprocessConfig:
    # Analysis thresholds
    SNR_THRESHOLD_DB: float = float(os.getenv("SNR_THRESHOLD_DB", "15.0"))
    CLIPPING_THRESHOLD: float = float(os.getenv("CLIPPING_THRESHOLD", "0.99"))
    LOW_SAMPLE_RATE_HZ: int = int(os.getenv("LOW_SAMPLE_RATE_HZ", "8000"))

    # Loudness normalization targets (EBU R128)
    TARGET_LUFS: float = float(os.getenv("TARGET_LUFS", "-23.0"))
    TARGET_TP: float = float(os.getenv("TARGET_TP", "-1.5"))
    TARGET_LRA: float = float(os.getenv("TARGET_LRA", "11.0"))

    # Observation Adding blend ratio for denoising
    OA_ALPHA: float = float(os.getenv("OA_ALPHA", "0.7"))

    # ASR
    ASR_ENGINE: str = os.getenv("ASR_ENGINE", "faster-whisper")
    ASR_MODEL_SIZE: str = os.getenv("ASR_MODEL_SIZE", "medium")
    ASR_DEVICE: str = os.getenv("ASR_DEVICE", "cpu")
    ASR_COMPUTE_TYPE: str = os.getenv("ASR_COMPUTE_TYPE", "int8")

    # Multilingual mode (Approach 3: Fine-Grained LID + Dual-Pass)
    LID_CONFIDENCE_THRESHOLD: float = float(os.getenv("LID_CONFIDENCE_THRESHOLD", "0.8"))
    LID_MIN_SPEECH_DURATION_MS: int = int(os.getenv("LID_MIN_SPEECH_DURATION_MS", "100"))

    # Speaker Diarization (Phase 2)
    DIARIZATION_MODEL: str = os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")
    DIARIZATION_DEVICE: str = os.getenv("DIARIZATION_DEVICE", "cpu")
    DIARIZATION_MIN_SPEAKERS: int = int(os.getenv("DIARIZATION_MIN_SPEAKERS", "2"))
    DIARIZATION_MAX_SPEAKERS: int = int(os.getenv("DIARIZATION_MAX_SPEAKERS", "10"))
    # Pyannote hyperparameters — tune for speaker splitting accuracy
    # Lower clustering_threshold = more aggressive speaker splitting (default 0.7046)
    # 0.0 means "use pyannote default" (no override)
    DIARIZATION_CLUSTERING_THRESHOLD: float = float(os.getenv("DIARIZATION_CLUSTERING_THRESHOLD", "0.0"))
    # Lower min_cluster_size = allow shorter speaker segments to form clusters (default 12)
    # 0 means "use pyannote default" (no override)
    DIARIZATION_MIN_CLUSTER_SIZE: int = int(os.getenv("DIARIZATION_MIN_CLUSTER_SIZE", "0"))
    # Speaker Embedding Verification (post-diarization cleanup)
    DIARIZATION_VERIFY_SPEAKERS: bool = os.getenv("DIARIZATION_VERIFY_SPEAKERS", "true").lower() == "true"
    DIARIZATION_EMBEDDING_MODEL: str = os.getenv("DIARIZATION_EMBEDDING_MODEL", "pyannote/wespeaker-voxceleb-resnet34-LM")
    DIARIZATION_MERGE_THRESHOLD: float = float(os.getenv("DIARIZATION_MERGE_THRESHOLD", "0.75"))
    DIARIZATION_OUTLIER_THRESHOLD: float = float(os.getenv("DIARIZATION_OUTLIER_THRESHOLD", "0.55"))
    DIARIZATION_REASSIGN_THRESHOLD: float = float(os.getenv("DIARIZATION_REASSIGN_THRESHOLD", "0.65"))
    DIARIZATION_REASSIGN_MARGIN: float = float(os.getenv("DIARIZATION_REASSIGN_MARGIN", "0.10"))
    DIARIZATION_MIN_EMBED_DURATION: float = float(os.getenv("DIARIZATION_MIN_EMBED_DURATION", "1.5"))
    # Long-segment windowed scan in _verify_segment_speakers (post-pyannote 4.0
    # migration). Catches mid-segment speaker changes that pyannote's longer
    # community-1 segments hide from start/end-only verification passes.
    # See plan/pyannote_4_migration.md §15.
    DIARIZATION_LONG_SEG_THRESHOLD: float = float(os.getenv("DIARIZATION_LONG_SEG_THRESHOLD", "8.0"))
    # WORD_GAP_MIN: original 0.20 was too strict for rapid back-and-forth
    # speech. Lowered to 0.10 (100 ms) — typical inter-sentence pauses in
    # fast conversation fall in the 80-150 ms range. Set higher if you see
    # over-splitting on continuous monologues.
    DIARIZATION_WORD_GAP_MIN: float = float(os.getenv("DIARIZATION_WORD_GAP_MIN", "0.10"))
    # OUTLIER_STRICT: own_sim must drop BELOW this to even consider a split.
    # 0.45 was conservative; 0.55 still well below the typical same-speaker
    # similarity (~0.75+) so within-speaker drift won't trigger, but catches
    # more genuine speaker changes.
    DIARIZATION_OUTLIER_STRICT: float = float(os.getenv("DIARIZATION_OUTLIER_STRICT", "0.55"))
    DIARIZATION_REASSIGN_STRICT: float = float(os.getenv("DIARIZATION_REASSIGN_STRICT", "0.60"))
    DIARIZATION_MARGIN_STRICT: float = float(os.getenv("DIARIZATION_MARGIN_STRICT", "0.25"))
    DIARIZATION_MAX_SPLITS_PER_SEG: int = int(os.getenv("DIARIZATION_MAX_SPLITS_PER_SEG", "2"))
    DIARIZATION_LONG_SEG_WINDOW: float = float(os.getenv("DIARIZATION_LONG_SEG_WINDOW", "2.0"))
    # When true, log EVERY probe in the long-segment scan (accepted or
    # rejected). Useful for diagnosing why expected splits aren't firing.
    # Turn off in production once thresholds are tuned (logs are noisy).
    DIARIZATION_LONG_SEG_DEBUG: bool = os.getenv("DIARIZATION_LONG_SEG_DEBUG", "true").lower() == "true"
    # Sliding-window speaker verify: run a second pass that checks the FIRST
    # 2 sec of each segment (in addition to the existing last-2s tail pass).
    # Catches "first portion mislabeled" cases that pyannote 4.0 can produce
    # when it merges a short utterance into a long adjacent same-cluster
    # segment (production repro May 12: SPEAKER_00 segment whose first ~3s
    # was actually Sathish, last ~6s was Vivek — tail pass passed because
    # the end was correctly Vivek). Default true. Set false to fall back
    # to tail-only behavior.
    DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL: bool = os.getenv(
        "DIARIZATION_SLIDING_WINDOW_BIDIRECTIONAL", "true"
    ).lower() == "true"
    # Pyannote 4.0 community-1 microservice on port 8012. Default flipped
    # from "inprocess" to "service" on May 11 after smoke test confirmed
    # 3.8x speedup with quality patches preserved. Dispatcher auto-falls
    # back to in-process pyannote 3.1 on ConnectError / HTTPStatusError.
    # See plan/pyannote_4_migration.md.
    DIARIZATION_PROVIDER: str = os.getenv("DIARIZATION_PROVIDER", "service")
    DIARIZATION_SERVICE_URL: str = os.getenv("DIARIZATION_SERVICE_URL", "http://localhost:8012")
    DIARIZATION_SERVICE_ENABLED: bool = os.getenv("DIARIZATION_SERVICE_ENABLED", "true").lower() == "true"
    DIARIZATION_MODEL_4: str = os.getenv("DIARIZATION_MODEL_4", "pyannote/speaker-diarization-community-1")
    # Granular HTTP timeouts. Fast-fail on connect (service down → fall back to
    # in-process 3.1 quickly). Generous read timeout once the request is in
    # flight — the service IS doing the work; a long read isn't a failure mode
    # we want to "retry" on the slower in-process path. ReadTimeout re-raises.
    DIARIZATION_SERVICE_CONNECT_TIMEOUT_S: float = float(os.getenv("DIARIZATION_SERVICE_CONNECT_TIMEOUT_S", "10"))
    DIARIZATION_SERVICE_READ_TIMEOUT_S: float = float(os.getenv("DIARIZATION_SERVICE_READ_TIMEOUT_S", "3600"))
    # Sliding window speaker verification (post-alignment)
    SLIDING_WINDOW_DURATION: float = float(os.getenv("SLIDING_WINDOW_DURATION", "1.5"))
    SLIDING_WINDOW_STEP: float = float(os.getenv("SLIDING_WINDOW_STEP", "0.5"))
    SLIDING_WINDOW_ZONE: float = float(os.getenv("SLIDING_WINDOW_ZONE", "3.0"))
    SLIDING_WINDOW_MARGIN: float = float(os.getenv("SLIDING_WINDOW_MARGIN", "0.15"))
    HF_AUTH_TOKEN: str = os.getenv("HF_AUTH_TOKEN", "")
    # Speaker boundary LLM verification
    SPEAKER_LLM_VERIFICATION: bool = os.getenv("SPEAKER_LLM_VERIFICATION", "true").lower() == "true"
    SPEAKER_LLM_MIN_WORDS: int = int(os.getenv("SPEAKER_LLM_MIN_WORDS", "3"))
    SPEAKER_LLM_MIN_DURATION: float = float(os.getenv("SPEAKER_LLM_MIN_DURATION", "1.5"))

    # Word-level alignment (Phase 3) — defaults to ASR_DEVICE so it runs on GPU when ASR does
    ALIGNMENT_DEVICE: str = os.getenv("ALIGNMENT_DEVICE", os.getenv("ASR_DEVICE", "cpu"))
    ALIGNMENT_BATCH_SIZE: int = int(os.getenv("ALIGNMENT_BATCH_SIZE", "16"))

    # Translation (IndicTrans2 for Indian languages)
    INDICTRANS2_MODEL: str = os.getenv("INDICTRANS2_MODEL", "ai4bharat/indictrans2-indic-en-dist-200M")
    INDICTRANS2_DEVICE: str = os.getenv("INDICTRANS2_DEVICE", "cpu")

    # Confidence-Based Re-transcription
    CONFIDENCE_RETRANSCRIBE_THRESHOLD: float = float(os.getenv("CONFIDENCE_RETRANSCRIBE_THRESHOLD", "-0.5"))
    CONFIDENCE_RETRANSCRIBE_BEAM_SIZE: int = int(os.getenv("CONFIDENCE_RETRANSCRIBE_BEAM_SIZE", "10"))
    CONFIDENCE_RETRANSCRIBE_BEST_OF: int = int(os.getenv("CONFIDENCE_RETRANSCRIBE_BEST_OF", "3"))
    CONFIDENCE_RETRANSCRIBE_MIN_REGION_SECONDS: float = float(os.getenv("CONFIDENCE_RETRANSCRIBE_MIN_REGION_SECONDS", "1.0"))
    CONFIDENCE_RETRANSCRIBE_MAX_RATIO: float = float(os.getenv("CONFIDENCE_RETRANSCRIBE_MAX_RATIO", "0.3"))

    # Vocabulary Correction (Phase 5a + 6a)
    VOCABULARY_DIR: str = os.getenv(
        "VOCABULARY_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vocabularies"),
    )
    VOCABULARY_CONFIDENCE_THRESHOLD: float = float(os.getenv("VOCABULARY_CONFIDENCE_THRESHOLD", "0.5"))
    VOCABULARY_HOTWORD_MAX_TOKENS: int = int(os.getenv("VOCABULARY_HOTWORD_MAX_TOKENS", "200"))

    # LLM Context Correction (Phase 5b)
    # Default: 3B for dev/CPU. For production (GPU), override: LLM_MODEL=qwen2.5:14b-instruct
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://localhost:11434")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen2.5:3b-instruct")
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "18000.0"))
    LLM_MAX_CANDIDATES_PER_BATCH: int = int(os.getenv("LLM_MAX_CANDIDATES_PER_BATCH", "50"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))

    # Punctuation refinement (Qwen-based, replaces oliverguhr)
    PUNCTUATION_REFINE_ENABLED: bool = os.getenv("PUNCTUATION_REFINE_ENABLED", "true").lower() == "true"
    PUNCTUATION_REFINE_BATCH_SIZE: int = int(os.getenv("PUNCTUATION_REFINE_BATCH_SIZE", "5"))

    # Intelligence Extraction (Phase 7) — Ollama (existing)
    # Default: 3B for dev/CPU. For production (GPU), override: INTELLIGENCE_LLM_MODEL=qwen2.5:14b-instruct
    INTELLIGENCE_ENABLED: bool = os.getenv("INTELLIGENCE_ENABLED", "false").lower() == "true"
    INTELLIGENCE_LLM_MODEL: str = os.getenv("INTELLIGENCE_LLM_MODEL", "qwen2.5:3b-instruct")
    INTELLIGENCE_MAX_SINGLE_PASS_TOKENS: int = int(os.getenv("INTELLIGENCE_MAX_SINGLE_PASS_TOKENS", "12000"))
    # Max cap for dynamic num_ctx — actual value is calculated per-call based on prompt size
    INTELLIGENCE_NUM_CTX: int = int(os.getenv("INTELLIGENCE_NUM_CTX", "16384"))

    # Polished P5 in Type 5 (high-quality re-run of P5 on corrected+diarized
    # transcript). When false, Type 5 reuses fast P5 (Type 7) as summary.json
    # by reading meetings/{id}/v1/summary_fast.json from R2. Saves ~3-4 min
    # per 1-hour meeting and ~5 LLM calls. Downstream consumers (email
    # preview/send, memoir fallback, speaker rename) see summary.json with
    # fast-P5 content — same Pydantic schema, slightly lower quality.
    # Set to "true" to restore polished P5 generation.
    POLISHED_P5_ENABLED: bool = os.getenv("POLISHED_P5_ENABLED", "false").lower() == "true"

    # Call threading (deal matching). When false, the intelligence pipeline
    # skips deal_matcher.match_call_to_deal() entirely. Effect:
    #   - intelligence.json: "deal_match": null
    #   - P3 + P4 prompts: previous_calls_context=None → "first call" branch
    #   - core_api.intelligence_store.process_deal_match: short-circuit return
    #   - meetings.memoir_deal_id / call_number_in_deal: stay NULL (nullable cols)
    #   - memoir_deals + deal_match_log: no new rows; existing rows untouched
    # Re-enable with DEAL_THREADING_ENABLED=true. No DB migration either way.
    DEAL_THREADING_ENABLED: bool = os.getenv("DEAL_THREADING_ENABLED", "false").lower() == "true"

    # ---- LangSmith tracing + LangChain Hub prompt management ----
    # See plan/langchain_langsmith_integration.md. All flags default off — zero
    # behavior change vs today until explicitly enabled.

    # LangSmith — tracing/observability for LLM calls
    LANGSMITH_ENABLED: bool = os.getenv("LANGSMITH_ENABLED", "false").lower() == "true"
    LANGSMITH_API_KEY: str = os.getenv("LANGSMITH_API_KEY", "")
    LANGSMITH_PROJECT: str = os.getenv("LANGSMITH_PROJECT", "memoir-dev")

    # LangChain Hub — prompt source (system + user text)
    LANGCHAIN_ENABLED: bool = os.getenv("LANGCHAIN_ENABLED", "false").lower() == "true"
    LANGCHAIN_HUB_TAG: str = os.getenv("LANGCHAIN_HUB_TAG", "production")
    LANGCHAIN_HUB_CACHE_TTL: int = int(os.getenv("LANGCHAIN_HUB_CACHE_TTL", "300"))
    LANGCHAIN_PREWARM_ON_STARTUP: bool = os.getenv(
        "LANGCHAIN_PREWARM_ON_STARTUP", "false"
    ).lower() == "true"
    # Hub owner handle. When set, prompts are pulled as "<owner>/<name>".
    # Required for private prompts in a workspace (free tier requires public
    # prompts under a personal handle). For Memoir's workspace we use the
    # public handle "memoir-ai".
    LANGCHAIN_HUB_OWNER: str = os.getenv("LANGCHAIN_HUB_OWNER", "")

    # Cross-provider routing (Option C — Phase 7a+). When true, a Hub prompt's
    # `model` / `temperature` / `max_tokens` fields drive execution. When false,
    # only system+user text comes from Hub; model selection stays in env.
    LANGCHAIN_RESPECT_HUB_MODEL: bool = os.getenv(
        "LANGCHAIN_RESPECT_HUB_MODEL", "false"
    ).lower() == "true"
    # What to do when Hub specifies a model that isn't available locally.
    # Only consulted when LANGCHAIN_RESPECT_HUB_MODEL=true; ignored otherwise.
    #   "pull"     → for Ollama only, attempt `ollama pull` synchronously (slow)
    #   "fallback" → use env default with warning
    #   "error"    → raise; pipeline fails for that prompt
    LANGCHAIN_ON_MISSING_MODEL: str = os.getenv("LANGCHAIN_ON_MISSING_MODEL", "pull")
    # Comma-separated fully-qualified model IDs available locally. Format:
    #   "<provider>:<model-id>". Examples:
    #   "claude:claude-sonnet-4-5,ollama:qwen2.5:14b,vllm:Qwen/Qwen2.5-14B-Instruct"
    # vast-start.sh reads this to pre-pull ollama:* entries and validate vllm:*
    # entries against VLLM_MODEL. Cloud entries require no startup action.
    LANGCHAIN_AVAILABLE_MODELS: str = os.getenv("LANGCHAIN_AVAILABLE_MODELS", "")

    # vLLM serves ONE model per instance, pinned at server start. Router uses
    # this to validate Hub `model=vllm:...` requests match what's actually loaded.
    VLLM_MODEL: str = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-14B-Instruct")

    # Multi-provider intelligence pipeline (Phase B) — Claude/Gemini
    # "ollama" = existing flow (above), "gemini" = free dev, "claude" = paid production
    INTELLIGENCE_PROVIDER: str = os.getenv("INTELLIGENCE_PROVIDER", "ollama")
    # Claude (production)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_HAIKU_MODEL: str = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
    CLAUDE_SONNET_MODEL: str = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
    CLAUDE_MAX_RETRIES: int = int(os.getenv("CLAUDE_MAX_RETRIES", "2"))
    CLAUDE_TIMEOUT: float = float(os.getenv("CLAUDE_TIMEOUT", "60.0"))
    # Gemini (dev/testing — FREE)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_MAX_RETRIES: int = int(os.getenv("GEMINI_MAX_RETRIES", "2"))
    GEMINI_TIMEOUT: float = float(os.getenv("GEMINI_TIMEOUT", "60.0"))
    # Groq (dev/testing — FREE, Llama models on custom hardware)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_FAST_MODEL: str = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
    GROQ_SMART_MODEL: str = os.getenv("GROQ_SMART_MODEL", "llama-3.3-70b-versatile")
    GROQ_MAX_RETRIES: int = int(os.getenv("GROQ_MAX_RETRIES", "2"))
    GROQ_TIMEOUT: float = float(os.getenv("GROQ_TIMEOUT", "60.0"))

    # Tier 5.1.a — vLLM (self-hosted OpenAI-compatible runtime). Replaces
    # Ollama with continuous-batching inference; same Qwen weights, free,
    # runs on the same GPU instance. Enable by setting INTELLIGENCE_PROVIDER=vllm.
    VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8011/v1")
    VLLM_MODEL: str = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct-AWQ")
    VLLM_FAST_MODEL: str = os.getenv("VLLM_FAST_MODEL", "")  # falls back to VLLM_MODEL
    VLLM_MAX_MODEL_LEN: int = int(os.getenv("VLLM_MAX_MODEL_LEN", "16384"))
    VLLM_GPU_MEM_UTILIZATION: float = float(os.getenv("VLLM_GPU_MEM_UTILIZATION", "0.45"))
    VLLM_MAX_RETRIES: int = int(os.getenv("VLLM_MAX_RETRIES", "2"))
    VLLM_TIMEOUT: float = float(os.getenv("VLLM_TIMEOUT", "300.0"))

    # Phone/VoIP Mode — sensitivity adjustments for muffled/quiet speakers
    PHONE_MODE_VAD_THRESHOLD: float = float(os.getenv("PHONE_MODE_VAD_THRESHOLD", "0.3"))
    PHONE_MODE_MIN_SPEECH_MS: int = int(os.getenv("PHONE_MODE_MIN_SPEECH_MS", "150"))
    PHONE_MODE_SPEECH_PAD_MS: int = int(os.getenv("PHONE_MODE_SPEECH_PAD_MS", "400"))
    PHONE_MODE_MIN_SILENCE_MS: int = int(os.getenv("PHONE_MODE_MIN_SILENCE_MS", "500"))

    # Phone/VoIP Mode — Tier 2: Spectral enhancement
    PHONE_MODE_TREBLE_GAIN: float = float(os.getenv("PHONE_MODE_TREBLE_GAIN", "6"))       # dB boost at target frequency
    PHONE_MODE_TREBLE_FREQ: int = int(os.getenv("PHONE_MODE_TREBLE_FREQ", "3000"))         # Hz — center frequency for treble shelf

    # Resemble Enhance Microservice
    RESEMBLE_ENHANCE_URL: str = os.getenv("RESEMBLE_ENHANCE_URL", "http://localhost:8010")

    # Timeouts (seconds)
    # Per-step FFmpeg timeout
    FFMPEG_TIMEOUT: int = int(os.getenv("FFMPEG_TIMEOUT", "18000"))          # 5 hours
    # Subprocess timeout (bandwidth extension, declipping)
    SUBPROCESS_TIMEOUT: int = int(os.getenv("SUBPROCESS_TIMEOUT", "18000"))  # 5 hours

    # Temp/output directory
    TEMP_DIR: str = os.getenv("PREPROCESS_TEMP_DIR", "/tmp/audio_preprocess")


preprocess_config = PreprocessConfig()
