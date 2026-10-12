"""
HTML UI for the audio preprocessing A/B comparison page.
"""


def get_html_page() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Audio Preprocessing — A/B Comparison</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1117;
            color: #e0e0e0;
            padding: 24px;
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 { color: #fff; margin-bottom: 6px; font-size: 24px; }
        .subtitle { color: #888; margin-bottom: 24px; font-size: 14px; }

        .upload-section {
            background: #1a1d27;
            border: 2px dashed #333;
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            margin-bottom: 24px;
            transition: border-color 0.2s;
        }
        .upload-section:hover { border-color: #4a9eff; }
        .upload-section.dragover { border-color: #4a9eff; background: #1e2233; }

        .upload-btn {
            background: #4a9eff;
            color: #fff;
            border: none;
            padding: 12px 32px;
            border-radius: 8px;
            font-size: 15px;
            cursor: pointer;
            margin-top: 12px;
        }
        .upload-btn:hover { background: #3a8eef; }
        .file-input { display: none; }
        .file-name { color: #4a9eff; margin-top: 8px; font-size: 13px; }

        .process-btn {
            background: #22c55e;
            color: #fff;
            border: none;
            padding: 14px 48px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            display: none;
            margin: 16px auto;
        }
        .process-btn:hover { background: #16a34a; }
        .process-btn:disabled { background: #555; cursor: not-allowed; }

        .progress {
            display: none;
            padding: 20px 0;
        }
        .progress-info {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            font-size: 14px;
            color: #4a9eff;
        }
        .progress-pct {
            font-weight: 700;
            font-size: 16px;
            color: #fff;
        }
        .progress-bar-bg {
            width: 100%;
            height: 8px;
            background: #22252f;
            border-radius: 4px;
            overflow: hidden;
        }
        .progress-bar-fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #4a9eff, #22c55e);
            border-radius: 4px;
            transition: width 0.5s ease;
        }
        .spinner {
            display: inline-block;
            width: 14px; height: 14px;
            border: 2px solid #333;
            border-top-color: #4a9eff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        .results { display: none; }

        .section-title {
            font-size: 16px;
            font-weight: 600;
            color: #fff;
            margin: 28px 0 12px 0;
            padding-bottom: 8px;
            border-bottom: 1px solid #2a2d37;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
            background: #1a1d27;
            border-radius: 8px;
            overflow: hidden;
        }
        th {
            background: #22252f;
            color: #aaa;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 12px 16px;
            text-align: left;
        }
        td {
            padding: 10px 16px;
            border-top: 1px solid #2a2d37;
            font-size: 14px;
            vertical-align: top;
        }
        tr:hover { background: #1e2130; }

        .definition {
            display: block;
            color: #777;
            font-size: 11px;
            margin-top: 3px;
            font-style: italic;
        }

        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-ok { background: #22c55e20; color: #4ade80; }
        .badge-applied { background: #4a9eff20; color: #4a9eff; }
        .badge-skipped { background: #33333380; color: #888; }
        .badge-triggered { background: #dc262620; color: #f87171; }

        .step-row td { padding: 8px 16px; }
        .step-reason {
            color: #999;
            font-size: 12px;
            line-height: 1.5;
            margin-top: 4px;
        }

        .comparison-table th:first-child { width: 25%; }
        .comparison-table td:nth-child(2),
        .comparison-table td:nth-child(3) { width: 37.5%; }
        .path-header { text-align: center; }
        .path-a-col { background: #1a2520; }
        .path-b-col { background: #1a1d2f; }

        .transcription-cell {
            max-height: 200px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-word;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 13px;
            line-height: 1.6;
        }

        audio { width: 100%; height: 40px; margin-top: 4px; }

        .segments-table {
            margin-top: 12px;
            font-size: 12px;
        }
        .segments-table th {
            padding: 8px 10px;
            font-size: 11px;
        }
        .segments-table td {
            padding: 6px 10px;
            font-size: 12px;
            font-family: 'SF Mono', Monaco, monospace;
        }
        .seg-time { color: #4a9eff; white-space: nowrap; }
        .seg-text { color: #e0e0e0; }
        .seg-meta { color: #666; font-size: 11px; }

        .expand-btn {
            background: none;
            border: none;
            color: #4a9eff;
            cursor: pointer;
            font-size: 14px;
            padding: 0 4px;
            line-height: 1;
            vertical-align: middle;
        }
        .expand-btn:hover { color: #60a5fa; }
        .word-row { display: none; }
        .word-row.visible { display: table-row; }
        .word-row td {
            background: #15171f !important;
            font-size: 11px !important;
            padding: 3px 10px !important;
            border-top: 1px solid #1e2130 !important;
        }
        .word-row:hover td { background: #1a1d27 !important; }

        .orig-word-row { display: none; }
        .orig-word-row.visible { display: table-row; }
        .orig-word-row td {
            background: #15171f !important;
            font-size: 11px !important;
            padding: 3px 10px !important;
            border-top: 1px solid #1e2130 !important;
        }
        .orig-word-row:hover td { background: #1a1d27 !important; }

        .path-selection {
            background: #1a1d27;
            border: 1px solid #2a2d37;
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 16px;
        }
        .radio-label {
            display: block;
            padding: 10px 14px;
            margin: 6px 0;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.15s;
        }
        .radio-label:hover { background: #22252f; }
        .radio-label input[type="radio"] {
            accent-color: #4a9eff;
            margin-right: 10px;
            vertical-align: middle;
        }
        .radio-text {
            color: #e0e0e0;
            font-size: 14px;
            font-weight: 500;
        }
        .radio-desc {
            display: block;
            color: #666;
            font-size: 12px;
            margin-left: 26px;
            margin-top: 2px;
        }
        .radio-label.disabled {
            opacity: 0.45;
            cursor: not-allowed;
            position: relative;
        }
        .radio-label.disabled:hover {
            opacity: 0.7;
        }
        .radio-label.disabled:hover .tooltip {
            display: block;
        }
        .radio-label.disabled input[type="radio"] { pointer-events: none; }
        .tooltip {
            display: none;
            position: relative;
            left: 26px;
            margin-top: 6px;
            background: #1c1007;
            border: 1px solid #b45309;
            color: #fbbf24;
            font-size: 12px;
            font-weight: 500;
            padding: 8px 14px;
            border-radius: 6px;
            z-index: 10;
            line-height: 1.5;
        }

        .options-row {
            display: flex;
            gap: 20px;
            margin-bottom: 16px;
        }
        .options-row > div { flex: 1; }
        .option-label {
            display: block;
            color: #aaa;
            font-size: 13px;
            margin-bottom: 8px;
        }
        .lang-select {
            width: 100%;
            padding: 10px 14px;
            background: #22252f;
            color: #e0e0e0;
            border: 1px solid #333;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            appearance: none;
            -webkit-appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23888' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 12px center;
        }
        .lang-select:focus { border-color: #4a9eff; outline: none; }

        .download-btn {
            background: #6366f1;
            color: #fff;
            border: none;
            padding: 10px 24px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            margin-top: 20px;
            display: none;
        }
        .download-btn:hover { background: #4f46e5; }

        .error-box {
            display: none;
            background: #dc262620;
            border: 1px solid #dc2626;
            color: #f87171;
            padding: 16px;
            border-radius: 8px;
            margin-top: 16px;
        }
    </style>
</head>
<body>
    <h1>Audio Preprocessing — A/B Comparison</h1>
    <p class="subtitle">
        Smart audio routing: analyze, process only what's needed, compare Path A (direct ASR) vs Path B (Resemble Enhanced ASR)
        &nbsp;&middot;&nbsp; <a href="/audio-preprocess/post-process" style="color:#4a9eff; text-decoration:none;">Post-Processing Test &rarr;</a>
    </p>

    <div class="upload-section" id="dropZone">
        <p style="color: #888; margin-bottom: 8px;">Drag & drop an audio file here, or</p>
        <button class="upload-btn" onclick="document.getElementById('fileInput').click()">Choose File</button>
        <input type="file" id="fileInput" class="file-input"
               accept=".mp3,.mp4,.m4a,.wav,.webm,.ogg,.flac,.aac" onchange="fileSelected(this)">
        <div class="file-name" id="fileName"></div>
    </div>

    <div class="options-row" id="optionsRow" style="display:none;">
        <div class="path-selection">
            <div style="color:#aaa; font-size:13px; margin-bottom:10px;">Select processing path:</div>
            <label class="radio-label">
                <input type="radio" name="processingMode" value="path_a" checked>
                <span class="radio-text">Path A — Smart Processed (Recommended)</span>
                <span class="radio-desc">Detection-first processing, direct ASR — faster, recommended for production</span>
            </label>
            <label class="radio-label" id="pathBLabel">
                <input type="radio" name="processingMode" value="path_b" id="pathBRadio">
                <span class="radio-text">Path B — Resemble Enhanced <span id="pathBStatus" style="font-size:11px; font-weight:400;"></span></span>
                <span class="radio-desc">Smart processing + Resemble Enhance before ASR — for perceptual quality testing</span>
            </label>
            <label class="radio-label" id="bothLabel">
                <input type="radio" name="processingMode" value="both" id="bothRadio">
                <span class="radio-text">Both Paths (A/B Comparison) <span id="bothStatus" style="font-size:11px; font-weight:400;"></span></span>
                <span class="radio-desc">Run both paths and compare results side by side</span>
                <span class="tooltip" id="bothTooltip">Processing both paths simultaneously takes significantly longer — select one path at a time for faster results</span>
            </label>
        </div>
        <div>
            <label class="option-label">ASR Engine:</label>
            <select id="engineSelect" class="lang-select">
                <option value="faster-whisper" selected>faster-whisper (CTranslate2 INT8)</option>
                <option value="openai-whisper">openai-whisper (PyTorch)</option>
            </select>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                faster-whisper uses optimized C++ inference with INT8 quantization.<br>
                Same model weights, identical accuracy, significantly faster on CPU.
            </div>
        </div>
        <div>
            <label class="option-label">Model Size:</label>
            <select id="modelSelect" class="lang-select">
                <option value="tiny">tiny (39M params)</option>
                <option value="base">base (74M params)</option>
                <option value="small">small (244M params)</option>
                <option value="medium" selected>medium (769M params)</option>
                <option value="large-v3-turbo">large-v3-turbo (809M params)</option>
                <option value="large-v3">large-v3 (1.5B params)</option>
            </select>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Larger models are more accurate but slower on CPU.<br>
                large-v3-turbo recommended — similar size to medium but ~11% more accurate.
            </div>
        </div>
        <div>
            <label class="option-label">ASR Language:</label>
            <select id="langSelect" class="lang-select">
                <option value="auto" selected>Auto-detect (Recommended)</option>
                <option value="en">English</option>
                <option value="ta">Tamil</option>
                <option value="hi">Hindi</option>
                <option value="te">Telugu</option>
                <option value="kn">Kannada</option>
                <option value="ml">Malayalam</option>
                <option value="mr">Marathi</option>
                <option value="bn">Bengali</option>
                <option value="gu">Gujarati</option>
                <option value="pa">Punjabi</option>
                <option value="ur">Urdu</option>
                <option value="fr">French</option>
                <option value="de">German</option>
                <option value="es">Spanish</option>
                <option value="ja">Japanese</option>
                <option value="zh">Chinese</option>
                <option value="ar">Arabic</option>
            </select>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Auto-detect lets Whisper identify the language per segment.<br>
                Force a language only if your audio is entirely in that language.
            </div>
        </div>
        <div>
            <label class="option-label">Multilingual Mode:</label>
            <label id="multilingualLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="multilingualCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Enable Fine-Grained LID</span>
            </label>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Splits audio into micro-segments, detects language per segment,<br>
                and uses dual-pass fallback for mixed-language regions.<br>
                <span id="multilingualNote" style="color:#fbbf24; display:none;">Only available when language is "Auto-detect".</span>
            </div>
        </div>
        <div>
            <label class="option-label">Phone/VoIP Mode:</label>
            <label id="phoneModeLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="phoneModeCheck" style="accent-color:#fbbf24; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Sensitive to Quiet/Muffled Speakers</span>
            </label>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Lowers speech detection thresholds and disables Whisper's<br>
                no-speech gating. Use for phone calls, VoIP recordings, or<br>
                any audio where one speaker is significantly quieter or muffled.
            </div>
        </div>
        <div>
            <label class="option-label">Speaker Diarization:</label>
            <label id="diarizationLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="diarizationCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Enable Speaker Identification</span>
            </label>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Identifies who spoke when using Pyannote AI.<br>
                Adds a "Speaker" column to segment results.
            </div>
            <div id="speakerCountDiv" style="display:none; margin-top:10px;">
                <label class="option-label">Expected Speakers:</label>
                <select id="speakerCountSelect" class="lang-select">
                    <option value="auto" selected>Auto-detect (Recommended)</option>
                    <option value="2">2 speakers</option>
                    <option value="3">3 speakers</option>
                    <option value="4">4 speakers</option>
                    <option value="5">5+ speakers</option>
                </select>
                <div style="color:#666; font-size:11px; margin-top:4px; line-height:1.5;">
                    Auto-detect works for most calls.<br>
                    Force count for better accuracy when known.
                </div>
            </div>
        </div>
        <div>
            <label class="option-label">Word-Level Alignment:</label>
            <label id="alignmentLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="alignmentCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Enable Word Timestamps</span>
            </label>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Uses ctc-forced-aligner (MMS-300M) to locate each word<br>
                in the audio with ~50ms accuracy. Adds expandable word<br>
                timestamps under each segment. Adds ~30-60s processing time.
            </div>
        </div>
        <div>
            <label class="option-label">Translation:</label>
            <label id="translateLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="translateCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Translate to English</span>
            </label>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                Indian languages (Tamil, Hindi, etc.) via IndicTrans2<br>
                for accurate text. Other languages via Whisper translate.<br>
                Word-level timestamps available for all translations.
            </div>
        </div>
        <div>
            <label style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="vocabEnabledCheck" style="accent-color:#4a9eff; width:18px; height:18px;"
                       onchange="toggleVocabSection()">
                <span style="color:#e0e0e0; font-size:14px;">Enable Vocabulary Correction</span>
            </label>
            <div id="vocabOptionsSection" style="display:none; margin-top:10px; padding:12px; background:#1a1d24;
                        border:1px solid #333; border-radius:8px;">
                <label class="option-label" style="margin-bottom:6px;">Vocabularies:</label>
                <div id="vocabCheckboxes" style="max-height:160px; overflow-y:auto; padding:8px;
                           background:#22252f; border:1px solid #333; border-radius:8px;">
                    <span style="color:#666; font-size:12px;">Loading vocabularies...</span>
                </div>
                <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                    Select one or more vocabularies for domain-specific correction.<br>
                    <a href="/audio-preprocess/vocabulary" target="_blank" style="color:#4a9eff;">Manage Vocabularies &rarr;</a>
                </div>
                <div style="margin-top:12px;">
                    <label style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                              background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                        <input type="checkbox" id="llmEnabledCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                        <span style="color:#e0e0e0; font-size:14px;">Enable LLM Correction</span>
                        <span id="llmStatusIndicator" style="font-size:11px; margin-left:auto;"></span>
                    </label>
                    <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                        Context-aware phonetic correction via Qwen 2.5 7B (Ollama).<br>
                        LLM reviews phonetic matches and decides based on context.
                    </div>
                </div>
            </div>
        </div>
        <div>
            <label class="option-label">Intelligence Extraction:</label>
            <label id="intelligenceLabel" style="display:flex; align-items:center; gap:10px; padding:10px 14px;
                      background:#22252f; border:1px solid #333; border-radius:8px; cursor:pointer;">
                <input type="checkbox" id="intelligenceCheck" style="accent-color:#4a9eff; width:18px; height:18px;">
                <span style="color:#e0e0e0; font-size:14px;">Enable Call Intelligence</span>
                <span id="intelligenceStatusIndicator" style="font-size:11px; margin-left:auto;"></span>
            </label>
            <div id="ppTypeRow" style="display:none; margin-top:8px; margin-left:4px;">
                <label style="font-size:12px; color:#888; margin-right:8px;">Post-Processing Type:</label>
                <select id="ppTypeSelect" style="background:#0f1117; color:#e0e0e0; border:1px solid #333; border-radius:6px; padding:5px 10px; font-size:13px;">
                    <option value="phase7">Phase 7 Intelligence</option>
                    <option value="memoir">Memoir Summary</option>
                </select>
            </div>
            <div style="color:#666; font-size:11px; margin-top:6px; line-height:1.5;">
                <span id="ppTypeDesc">Extracts competitors, objections, next steps, sentiment,<br>
                MEDDIC/BANT scoring, deal health via LLM (Ollama).<br>
                Requires diarization for coaching metrics.</span>
            </div>
        </div>
    </div>

    <button class="process-btn" id="processBtn" onclick="processAudio()">Process Audio</button>

    <div class="progress" id="progress">
        <div class="progress-info">
            <span><span class="spinner"></span><span id="progressText">Analyzing audio...</span></span>
            <span class="progress-pct" id="progressPct">0%</span>
        </div>
        <div class="progress-bar-bg">
            <div class="progress-bar-fill" id="progressBar"></div>
        </div>
    </div>

    <div class="error-box" id="errorBox"></div>

    <div class="results" id="results">

        <!-- Input Analysis -->
        <div class="section-title">Input Audio Analysis</div>
        <table id="analysisTable">
            <thead><tr><th>Metric</th><th>Value</th><th>Action Triggered</th></tr></thead>
            <tbody></tbody>
        </table>

        <!-- Processing Steps -->
        <div class="section-title">Processing Steps</div>
        <table id="stepsTable">
            <thead><tr><th style="width:25%">Step</th><th style="width:12%">Status</th><th>Reason</th></tr></thead>
            <tbody></tbody>
        </table>

        <!-- Diarization Summary (shown only when diarization enabled) -->
        <div id="diarizationSummary" style="display:none;"></div>
        <div id="vocabularySummary" style="display:none;"></div>
        <div id="llmCorrectionSummary" style="display:none;"></div>
        <div id="intelligenceSection" style="display:none;"></div>

        <!-- A/B Comparison / Single Path Results -->
        <div class="section-title" id="comparisonTitle">A/B Comparison</div>
        <div id="comparisonContainer"></div>

        <!-- Timestamp Segments -->
        <div id="segmentsASection" style="display:none;">
            <div class="section-title">Segment Timestamps — Path A (Smart Processed)</div>
            <div id="segmentsA"></div>
        </div>

        <div id="origTimestampsASection" style="display:none;">
            <div class="section-title">Original Audio Timestamps — Path A</div>
            <div id="origTimestampsA"></div>
        </div>

        <div id="segmentsBSection" style="display:none;">
            <div class="section-title">Segment Timestamps — Path B (Resemble Enhanced)</div>
            <div id="segmentsB"></div>
        </div>

        <div id="origTimestampsBSection" style="display:none;">
            <div class="section-title">Original Audio Timestamps — Path B</div>
            <div id="origTimestampsB"></div>
        </div>

        <button class="download-btn" id="downloadPdf" onclick="downloadPDF()">Download Results as PDF</button>

    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.8.2/jspdf.plugin.autotable.min.js"></script>

    <script>
        const dropZone = document.getElementById('dropZone');
        let selectedFile = null;
        let lastResultData = null;

        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
        dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                selectedFile = e.dataTransfer.files[0];
                document.getElementById('fileName').textContent = selectedFile.name +
                    ' (' + (selectedFile.size / 1024 / 1024).toFixed(1) + ' MB)';
                document.getElementById('optionsRow').style.display = 'flex';
                document.getElementById('processBtn').style.display = 'block';
            }
        });

        function fileSelected(input) {
            if (input.files.length) {
                selectedFile = input.files[0];
                document.getElementById('fileName').textContent = selectedFile.name +
                    ' (' + (selectedFile.size / 1024 / 1024).toFixed(1) + ' MB)';
                document.getElementById('optionsRow').style.display = 'flex';
                document.getElementById('processBtn').style.display = 'block';
            }
        }

        // Multilingual mode toggle — only available when language is "auto"
        (function() {
            const langSelect = document.getElementById('langSelect');
            const multilingualCheck = document.getElementById('multilingualCheck');
            const multilingualLabel = document.getElementById('multilingualLabel');
            const multilingualNote = document.getElementById('multilingualNote');

            function updateMultilingualState() {
                const isAuto = langSelect.value === 'auto';
                multilingualCheck.disabled = !isAuto;
                if (!isAuto) multilingualCheck.checked = false;
                multilingualNote.style.display = isAuto ? 'none' : 'inline';
                multilingualLabel.style.opacity = isAuto ? '1' : '0.45';
                multilingualLabel.style.cursor = isAuto ? 'pointer' : 'not-allowed';
            }

            langSelect.addEventListener('change', updateMultilingualState);
            updateMultilingualState();
        })();

        // Diarization checkbox toggle — show/hide speaker count dropdown
        document.getElementById('diarizationCheck').addEventListener('change', function() {
            document.getElementById('speakerCountDiv').style.display = this.checked ? 'block' : 'none';
        });

        // Resemble Enhance microservice availability check
        let enhanceAvailable = false;
        (async function checkEnhanceService() {
            try {
                const resp = await fetch('/audio-preprocess/enhance-status');
                const data = await resp.json();
                enhanceAvailable = data.available;
            } catch (e) {
                enhanceAvailable = false;
            }

            const pathBLabel = document.getElementById('pathBLabel');
            const pathBRadio = document.getElementById('pathBRadio');
            const pathBStatus = document.getElementById('pathBStatus');
            const bothLabel = document.getElementById('bothLabel');
            const bothRadio = document.getElementById('bothRadio');
            const bothStatus = document.getElementById('bothStatus');

            if (!enhanceAvailable) {
                pathBRadio.disabled = true;
                pathBLabel.classList.add('disabled');
                pathBStatus.innerHTML = '<span style="color:#f87171;">(Service unavailable)</span>';

                bothRadio.disabled = true;
                bothLabel.classList.add('disabled');
                bothStatus.innerHTML = '<span style="color:#f87171;">(Service unavailable)</span>';
            } else {
                pathBRadio.disabled = false;
                pathBLabel.classList.remove('disabled');
                pathBStatus.innerHTML = '<span style="color:#4ade80;">(Service connected)</span>';

                bothRadio.disabled = false;
                bothLabel.classList.remove('disabled');
                bothStatus.innerHTML = '<span style="color:#4ade80;">(Service connected)</span>';
            }
        })();

        // Load available vocabularies as checkboxes
        (async function loadVocabularies() {
            try {
                const resp = await fetch('/audio-preprocess/vocabulary/list');
                const data = await resp.json();
                const container = document.getElementById('vocabCheckboxes');
                if (!data.vocabularies || data.vocabularies.length === 0) {
                    container.innerHTML = '<span style="color:#666; font-size:12px;">No vocabularies found. <a href="/audio-preprocess/vocabulary" target="_blank" style="color:#4a9eff;">Create one</a></span>';
                    return;
                }
                container.innerHTML = '';
                for (const v of data.vocabularies) {
                    const label = document.createElement('label');
                    label.style.cssText = 'display:flex; align-items:center; gap:8px; padding:6px 8px; cursor:pointer; border-radius:4px;';
                    label.onmouseenter = function() { this.style.background = '#2a2d37'; };
                    label.onmouseleave = function() { this.style.background = 'transparent'; };
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.value = v.name;
                    cb.className = 'vocab-checkbox';
                    cb.style.cssText = 'accent-color:#4a9eff; width:16px; height:16px;';
                    const text = document.createElement('span');
                    text.style.cssText = 'color:#e0e0e0; font-size:13px;';
                    text.textContent = v.display_name + ' (' + v.entry_count + ' terms)';
                    label.appendChild(cb);
                    label.appendChild(text);
                    container.appendChild(label);
                }
            } catch (e) {
                console.warn('Could not load vocabularies:', e);
            }
        })();

        // Check LLM (Ollama) availability
        (async function checkLLMStatus() {
            const indicator = document.getElementById('llmStatusIndicator');
            try {
                const resp = await fetch('/audio-preprocess/llm-status');
                const data = await resp.json();
                if (data.available) {
                    indicator.innerHTML = '<span style="color:#4ade80;">Connected</span>';
                } else {
                    indicator.innerHTML = '<span style="color:#f87171;">Unavailable</span>';
                }
            } catch (e) {
                indicator.innerHTML = '<span style="color:#f87171;">Unavailable</span>';
            }
        })();

        // Check Intelligence LLM availability
        (async function checkIntelligenceStatus() {
            const indicator = document.getElementById('intelligenceStatusIndicator');
            try {
                const resp = await fetch('/audio-preprocess/intelligence-status');
                const data = await resp.json();
                if (data.available) {
                    indicator.innerHTML = '<span style="color:#4ade80;">Connected (' + escapeHtml(data.model) + ')</span>';
                } else {
                    indicator.innerHTML = '<span style="color:#f87171;">Unavailable</span>';
                }
            } catch (e) {
                indicator.innerHTML = '<span style="color:#f87171;">Unavailable</span>';
            }
        })();

        // Show/hide post-processing type dropdown when intelligence checkbox toggles
        document.getElementById('intelligenceCheck').addEventListener('change', function() {
            document.getElementById('ppTypeRow').style.display = this.checked ? 'block' : 'none';
        });
        // Update description when ppType changes
        document.getElementById('ppTypeSelect').addEventListener('change', function() {
            const desc = document.getElementById('ppTypeDesc');
            if (this.value === 'memoir') {
                desc.innerHTML = 'Memoir: CRM-ready summary with confidence tags, source attribution,<br>discovery fields (budget, authority, need, timeline, tech stack, team, competition),<br>action items split by owner, buying/risk signals.';
            } else {
                desc.innerHTML = 'Extracts competitors, objections, next steps, sentiment,<br>MEDDIC/BANT scoring, deal health via LLM (Ollama).<br>Requires diarization for coaching metrics.';
            }
        });

        function toggleVocabSection() {
            const checked = document.getElementById('vocabEnabledCheck').checked;
            document.getElementById('vocabOptionsSection').style.display = checked ? 'block' : 'none';
            if (!checked) {
                document.getElementById('llmEnabledCheck').checked = false;
            }
        }

        async function processAudio() {
            if (!selectedFile) return;
            // Validate: if vocabulary correction is enabled, at least one vocabulary must be selected
            const vocabCheck = document.getElementById('vocabEnabledCheck');
            const selectedVocabs = [...document.querySelectorAll('.vocab-checkbox:checked')].map(cb => cb.value);
            if (vocabCheck.checked && selectedVocabs.length === 0) {
                alert('Please select at least one vocabulary when Vocabulary Correction is enabled.');
                return;
            }
            const btn = document.getElementById('processBtn');
            const progress = document.getElementById('progress');
            const results = document.getElementById('results');
            const errorBox = document.getElementById('errorBox');

            btn.disabled = true;
            progress.style.display = 'block';
            results.style.display = 'none';
            errorBox.style.display = 'none';

            const mode = document.querySelector('input[name="processingMode"]:checked').value;

            const engineLabel = document.getElementById('engineSelect').value;
            const modelLabel = document.getElementById('modelSelect').value;
            const asrLabel = engineLabel + ' (' + modelLabel + ')';

            const steps = [];
            steps.push({ text: 'Analyzing audio...', pct: 5 });
            steps.push({ text: 'Resampling to 16kHz mono...', pct: 10 });
            steps.push({ text: 'Applying highpass 80Hz filter (FFmpeg)...', pct: 18 });
            steps.push({ text: 'Running Silero VAD silence trimming...', pct: 30 });
            steps.push({ text: 'Normalizing loudness — dual-pass (FFmpeg)...', pct: 42 });
            steps.push({ text: 'Checking conditional processing...', pct: 48 });
            const isDiarization = document.getElementById('diarizationCheck').checked;
            if (isDiarization) {
                steps.push({ text: 'Running speaker diarization (Pyannote)...', pct: 55 });
            }
            const isMultilingual = document.getElementById('multilingualCheck').checked;
            if (isMultilingual) {
                steps.push({ text: 'Running fine-grained language identification...', pct: 55 });
                steps.push({ text: 'Grouping segments by language...', pct: 60 });
            }
            const isAlignment = document.getElementById('alignmentCheck').checked;
            const isTranslation = document.getElementById('translateCheck').checked;
            const isIntelligence = document.getElementById('intelligenceCheck').checked;
            if (mode === 'path_a') {
                steps.push({ text: 'Running ' + asrLabel + ' — Path A...', pct: 68 });
                if (isTranslation) {
                    steps.push({ text: 'Translating non-English segments to English...', pct: 78 });
                }
                if (isAlignment) {
                    steps.push({ text: 'Running word-level alignment (MMS-300M)...', pct: 83 });
                }
                if (isIntelligence) {
                    steps.push({ text: 'Computing coaching metrics...', pct: 88 });
                    steps.push({ text: 'Running intelligence extraction (LLM)...', pct: 92 });
                }
                steps.push({ text: 'Preparing results...', pct: 95 });
            } else if (mode === 'path_b') {
                steps.push({ text: 'Running Resemble Enhance (diffusion model)...', pct: 62 });
                steps.push({ text: 'Running ' + asrLabel + ' — Path B...', pct: 75 });
                if (isTranslation) {
                    steps.push({ text: 'Translating non-English segments to English...', pct: 82 });
                }
                if (isAlignment) {
                    steps.push({ text: 'Running word-level alignment (MMS-300M)...', pct: 86 });
                }
                if (isIntelligence) {
                    steps.push({ text: 'Computing coaching metrics...', pct: 90 });
                    steps.push({ text: 'Running intelligence extraction (LLM)...', pct: 93 });
                }
                steps.push({ text: 'Preparing results...', pct: 95 });
            } else {
                steps.push({ text: 'Running ' + asrLabel + ' — Path A...', pct: 55 });
                steps.push({ text: 'Running Resemble Enhance (diffusion model)...', pct: 65 });
                steps.push({ text: 'Running ' + asrLabel + ' — Path B...', pct: 76 });
                if (isTranslation) {
                    steps.push({ text: 'Translating non-English segments to English...', pct: 82 });
                }
                if (isAlignment) {
                    steps.push({ text: 'Running word-level alignment (MMS-300M)...', pct: 86 });
                }
                if (isIntelligence) {
                    steps.push({ text: 'Computing coaching metrics...', pct: 90 });
                    steps.push({ text: 'Running intelligence extraction (LLM)...', pct: 93 });
                }
                steps.push({ text: 'Preparing results...', pct: 95 });
            }

            let stepIdx = 0;
            updateProgress(steps[0].text, steps[0].pct);
            const interval = setInterval(() => {
                stepIdx = Math.min(stepIdx + 1, steps.length - 1);
                updateProgress(steps[stepIdx].text, steps[stepIdx].pct);
            }, 4000);

            try {
                const lang = document.getElementById('langSelect').value;
                const engine = document.getElementById('engineSelect').value;
                const modelSize = document.getElementById('modelSelect').value;
                const formData = new FormData();
                formData.append('file', selectedFile);
                formData.append('processing_mode', mode);
                formData.append('language', lang);
                formData.append('engine', engine);
                formData.append('model_size', modelSize);
                formData.append('multilingual', document.getElementById('multilingualCheck').checked ? 'true' : 'false');
                formData.append('diarization', document.getElementById('diarizationCheck').checked ? 'true' : 'false');
                formData.append('num_speakers', document.getElementById('diarizationCheck').checked
                    ? document.getElementById('speakerCountSelect').value : 'auto');
                formData.append('alignment', document.getElementById('alignmentCheck').checked ? 'true' : 'false');
                formData.append('translate_to_english', document.getElementById('translateCheck').checked ? 'true' : 'false');
                const vocabEnabled = document.getElementById('vocabEnabledCheck').checked;
                formData.append('vocab_correction', vocabEnabled ? 'true' : 'false');
                formData.append('vocabulary', vocabEnabled ? selectedVocabs.join(',') : '');
                formData.append('llm_correction', (vocabEnabled && document.getElementById('llmEnabledCheck').checked) ? 'true' : 'false');
                formData.append('enable_intelligence', document.getElementById('intelligenceCheck').checked ? 'true' : 'false');
                formData.append('post_processing_type', document.getElementById('intelligenceCheck').checked ? document.getElementById('ppTypeSelect').value : 'phase7');
                formData.append('phone_mode', document.getElementById('phoneModeCheck').checked ? 'true' : 'false');
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5 * 60 * 60 * 1000); // 5 hours
                const response = await fetch('/audio-preprocess/process', { method: 'POST', body: formData, signal: controller.signal });
                clearTimeout(timeoutId);
                clearInterval(interval);
                if (!response.ok) { const err = await response.json(); throw new Error(err.detail || 'Processing failed'); }
                updateProgress('Complete!', 100);
                const data = await response.json();
                lastResultData = data;
                renderResults(data);
                document.getElementById('downloadPdf').style.display = 'inline-block';
            } catch (e) {
                clearInterval(interval);
                errorBox.textContent = 'Error: ' + e.message;
                errorBox.style.display = 'block';
            } finally {
                setTimeout(() => { progress.style.display = 'none'; }, 600);
                btn.disabled = false;
            }
        }

        function updateProgress(text, pct) {
            document.getElementById('progressText').textContent = text;
            document.getElementById('progressPct').textContent = pct + '%';
            document.getElementById('progressBar').style.width = pct + '%';
        }

        const METRIC_DEFS = {
            'File': 'Name of the uploaded audio file.',
            'Sample Rate': 'Number of audio samples captured per second (Hz). Higher = more frequency detail. Whisper needs 16kHz.',
            'Duration': 'Total length of the raw audio file in seconds.',
            'Channels': 'Number of audio channels. 1 = mono, 2 = stereo. Whisper requires mono.',
            'Bit Depth': 'Bits per sample. PCM_16 = 16-bit integer, giving ~96 dB dynamic range. Standard for WAV.',
            'Peak Amplitude': 'Loudest sample on a 0–1 scale. 1.0 = absolute digital maximum.',
            'RMS Amplitude': 'Root Mean Square — average loudness level. Speech typically has low RMS due to pauses between words.',
            'SNR': 'Signal-to-Noise Ratio (dB). How much louder speech is vs background noise. >25 dB = clean, <15 dB = noisy.',
            'Clipping': 'Percentage of samples at maximum amplitude. >0.1% = distorted flat-topped waveforms that hurt ASR.',
        };

        function renderResults(data) {
            document.getElementById('results').style.display = 'block';
            const a = data.input_analysis;

            // ── Analysis Table ──
            const tbody = document.querySelector('#analysisTable tbody');
            tbody.innerHTML = '';
            const rows = [
                ['File', a.file_name, '-'],
                ['Sample Rate', a.sample_rate + ' Hz',
                    a.needs_bandwidth_extension
                        ? '<span class="badge badge-triggered">Bandwidth Extension triggered</span>'
                        : '<span class="badge badge-ok">OK — above 8kHz threshold</span>'],
                ['Duration', a.duration_seconds + 's', '-'],
                ['Channels', a.channels, '-'],
                ['Bit Depth', a.bit_depth, '-'],
                ['Peak Amplitude', a.peak_amplitude, '-'],
                ['RMS Amplitude', a.rms_amplitude, '-'],
                ['SNR', a.snr_db + ' dB',
                    a.needs_denoising
                        ? '<span class="badge badge-triggered">Denoise + OA triggered</span>'
                        : '<span class="badge badge-ok">OK — above 15 dB threshold</span>'],
                ['Clipping', a.clipping_percentage + '%',
                    a.needs_declipping
                        ? '<span class="badge badge-triggered">Declipping triggered</span>'
                        : '<span class="badge badge-ok">OK — below 0.1% threshold</span>'],
            ];
            rows.forEach(([metric, value, action]) => {
                const def = METRIC_DEFS[metric] || '';
                const tr = document.createElement('tr');
                tr.innerHTML = `<td>${metric}${def ? '<span class="definition">' + def + '</span>' : ''}</td><td>${value}</td><td>${action}</td>`;
                tbody.appendChild(tr);
            });

            // ── Steps Table ──
            const stepsBody = document.querySelector('#stepsTable tbody');
            stepsBody.innerHTML = '';
            data.steps.forEach(s => {
                const statusBadge = s.status === 'applied'
                    ? '<span class="badge badge-applied">Applied</span>'
                    : '<span class="badge badge-skipped">Skipped</span>';
                const tr = document.createElement('tr');
                tr.className = 'step-row';
                tr.innerHTML = `<td>${s.step}</td><td>${statusBadge}</td><td><div class="step-reason">${escapeHtml(s.reason)}</div></td>`;
                stepsBody.appendChild(tr);
            });

            // ── Diarization Summary (if enabled) ──
            const diarDiv = document.getElementById('diarizationSummary');
            if (data.diarization && data.diarization.enabled) {
                const d = data.diarization;
                diarDiv.style.display = 'block';
                let diarRows =
                    '<tr><td>Speakers Detected</td><td><strong>' + d.num_speakers + '</strong></td></tr>' +
                    '<tr><td>Method</td><td>' + (d.method === 'multichannel' ? 'Multichannel (channel-based)' : 'Pyannote AI') + '</td></tr>' +
                    '<tr><td>Diarization Segments</td><td>' + d.speakers.length + '</td></tr>' +
                    '<tr><td>Processing Time</td><td>' + d.processing_time_seconds + 's</td></tr>';
                if (d.verification) {
                    const v = d.verification;
                    diarRows += '<tr><td>Embedding Verification</td><td>Enabled (' +
                        v.merged_clusters + ' merged, ' + v.segments_reassigned + ' reassigned, ' +
                        v.short_segments_inherited + ' inherited, ' + v.processing_time_seconds + 's)</td></tr>' +
                        '<tr><td>Clusters</td><td>' + v.original_clusters + ' &rarr; ' + v.final_clusters + '</td></tr>';
                }
                diarDiv.innerHTML = '<div class="section-title">Speaker Diarization Summary</div>' +
                    '<table><thead><tr><th>Detail</th><th>Value</th></tr></thead><tbody>' +
                    diarRows + '</tbody></table>';
            } else {
                diarDiv.style.display = 'none';
            }

            // ── Vocabulary Corrections Summary (Phase 5a — if enabled) ──
            const vocabDiv = document.getElementById('vocabularySummary');
            if (data.vocabulary && data.vocabulary.enabled) {
                const v = data.vocabulary;
                const corrections = [...(v.corrections_a || []), ...(v.corrections_b || [])];
                let html = '<div class="section-title">Vocabulary Corrections (5a — Rule-Based)</div>' +
                    '<table><thead><tr><th>Detail</th><th>Value</th></tr></thead><tbody>' +
                    '<tr><td>Vocabulary</td><td><strong>' + escapeHtml(v.name) + '</strong></td></tr>' +
                    '<tr><td>Corrections Applied</td><td>' + corrections.length + '</td></tr>' +
                    '<tr><td>Processing Time</td><td>' + v.processing_time_seconds + 's</td></tr>' +
                    '</tbody></table>';
                if (corrections.length > 0) {
                    html += '<table style="margin-top:10px;"><thead><tr>' +
                        '<th>Original</th><th>Corrected To</th><th>Type</th><th>Field</th><th>Match Score</th>' +
                        '</tr></thead><tbody>';
                    corrections.forEach(c => {
                        html += '<tr><td><code style="color:#f87171;">' + escapeHtml(c.original_text) + '</code></td>' +
                            '<td><code style="color:#4ade80;">' + escapeHtml(c.corrected_text) + '</code></td>' +
                            '<td>' + escapeHtml(c.correction_type) + '</td>' +
                            '<td>' + escapeHtml(c.field || 'text') + '</td>' +
                            '<td>' + (c.match_score || '—') + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }
                vocabDiv.style.display = 'block';
                vocabDiv.innerHTML = html;
            } else {
                vocabDiv.style.display = 'none';
            }

            // ── LLM Context Corrections Summary (Phase 5b — if enabled) ──
            const llmDiv = document.getElementById('llmCorrectionSummary');
            if (data.llm_correction && data.llm_correction.enabled) {
                const llm = data.llm_correction;
                const allLlm = [...(llm.corrections_a || []), ...(llm.corrections_b || [])];
                const applied = allLlm.filter(c => c.correction_type === 'llm_context');
                const rejected = allLlm.filter(c => c.correction_type === 'llm_rejected');
                const kept = allLlm.filter(c => c.correction_type === 'llm_keep');
                const invalid = allLlm.filter(c => c.correction_type === 'llm_invalid');
                const timedOut = allLlm.filter(c => c.correction_type === 'llm_timeout');

                let html = '<div class="section-title">LLM Context Corrections (5b — Ollama)</div>' +
                    '<table><thead><tr><th>Detail</th><th>Value</th></tr></thead><tbody>' +
                    '<tr><td>Status</td><td>' + (llm.available ? '<span style="color:#4ade80;">Connected</span>' : '<span style="color:#f87171;">Unavailable</span>') + '</td></tr>' +
                    '<tr><td>Candidates Reviewed</td><td>' + allLlm.length + '</td></tr>' +
                    '<tr><td>Corrections Applied</td><td><strong>' + applied.length + '</strong></td></tr>' +
                    '<tr><td>Kept as-is (correct)</td><td>' + kept.length + '</td></tr>' +
                    '<tr><td>Rejected (phonetic mismatch)</td><td>' + rejected.length + '</td></tr>';
                if (invalid.length > 0) html += '<tr><td>Invalid (not in vocabulary)</td><td>' + invalid.length + '</td></tr>';
                if (timedOut.length > 0) html += '<tr><td>Timed Out</td><td>' + timedOut.length + '</td></tr>';
                html += '<tr><td>Processing Time</td><td>' + llm.processing_time_seconds + 's</td></tr>' +
                    '</tbody></table>';

                // Applied corrections table
                if (applied.length > 0) {
                    html += '<div style="margin-top:10px; font-weight:bold; color:#4ade80;">Applied Corrections:</div>' +
                        '<table style="margin-top:6px;"><thead><tr>' +
                        '<th>Original</th><th>Corrected To</th><th>Field</th><th>Confidence</th><th>LLM Reasoning</th>' +
                        '</tr></thead><tbody>';
                    applied.forEach(c => {
                        html += '<tr><td><code style="color:#f87171;">' + escapeHtml(c.original_text) + '</code></td>' +
                            '<td><code style="color:#4ade80;">' + escapeHtml(c.corrected_text) + '</code></td>' +
                            '<td>' + escapeHtml(c.field || 'text') + '</td>' +
                            '<td>' + (c.confidence_before || '—') + '</td>' +
                            '<td style="font-size:11px; color:#999;">' + escapeHtml(c.llm_reasoning || '') + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }

                // Rejected corrections table (for transparency)
                if (rejected.length > 0) {
                    html += '<div style="margin-top:10px; font-weight:bold; color:#f87171;">Rejected (phonetic verification failed):</div>' +
                        '<table style="margin-top:6px;"><thead><tr>' +
                        '<th>Original</th><th>LLM Suggested</th><th>Field</th><th>Reasoning</th>' +
                        '</tr></thead><tbody>';
                    rejected.forEach(c => {
                        html += '<tr><td><code>' + escapeHtml(c.original_text) + '</code></td>' +
                            '<td><code style="color:#fbbf24;">' + escapeHtml(c.corrected_text) + '</code></td>' +
                            '<td>' + escapeHtml(c.field || 'text') + '</td>' +
                            '<td style="font-size:11px; color:#999;">' + escapeHtml(c.llm_reasoning || '') + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }

                llmDiv.style.display = 'block';
                llmDiv.innerHTML = html;
            } else {
                llmDiv.style.display = 'none';
            }

            // ── Intelligence / Memoir Results (if enabled) ──
            const intelDiv = document.getElementById('intelligenceSection');
            if (data.intelligence && data.intelligence.enabled && data.intelligence.extraction && Object.keys(data.intelligence.extraction).length > 0) {
                intelDiv.style.display = 'block';
                if (data.intelligence.post_processing_type === 'memoir') {
                    intelDiv.innerHTML = renderMemoirSummary(data.intelligence);
                } else {
                    intelDiv.innerHTML = renderIntelligence(data.intelligence);
                }
            } else {
                intelDiv.style.display = 'none';
            }

            // ── Comparison / Results Table ──
            const container = document.getElementById('comparisonContainer');
            container.innerHTML = '';
            const reqId = data.request_id;
            const pa = data.path_a;
            const pb = data.path_b;
            const mode = data.processing_mode || 'both';

            const titleEl = document.getElementById('comparisonTitle');
            const segASection = document.getElementById('segmentsASection');
            const segBSection = document.getElementById('segmentsBSection');
            const origASection = document.getElementById('origTimestampsASection');
            const origBSection = document.getElementById('origTimestampsBSection');
            segASection.style.display = 'none';
            segBSection.style.display = 'none';
            origASection.style.display = 'none';
            origBSection.style.display = 'none';

            if (mode === 'both') {
                titleEl.textContent = 'A/B Comparison';
                container.innerHTML = buildComparisonTable(reqId, pa, pb);
                segASection.style.display = 'block';
                segBSection.style.display = 'block';
                document.getElementById('segmentsA').innerHTML = renderSegmentsTable(pa.segments);
                document.getElementById('segmentsB').innerHTML = renderSegmentsTable(pb.segments);
                const origHtmlA = renderOriginalTimestampsTable(pa.segments);
                const origHtmlB = renderOriginalTimestampsTable(pb.segments);
                if (origHtmlA) { origASection.style.display = 'block'; document.getElementById('origTimestampsA').innerHTML = origHtmlA; }
                if (origHtmlB) { origBSection.style.display = 'block'; document.getElementById('origTimestampsB').innerHTML = origHtmlB; }
            } else if (mode === 'path_a') {
                titleEl.textContent = 'Path A — Smart Processed Results';
                container.innerHTML = buildSinglePathTable(reqId, pa, 'Path A — Smart Processed');
                segASection.style.display = 'block';
                document.getElementById('segmentsA').innerHTML = renderSegmentsTable(pa.segments);
                const origHtmlA = renderOriginalTimestampsTable(pa.segments);
                if (origHtmlA) { origASection.style.display = 'block'; document.getElementById('origTimestampsA').innerHTML = origHtmlA; }
            } else if (mode === 'path_b') {
                titleEl.textContent = 'Path B — Resemble Enhanced Results';
                container.innerHTML = buildSinglePathTable(reqId, pb, 'Path B — Resemble Enhanced', pb.resemble_available);
                segBSection.style.display = 'block';
                document.getElementById('segmentsB').innerHTML = renderSegmentsTable(pb.segments);
                const origHtmlB = renderOriginalTimestampsTable(pb.segments);
                if (origHtmlB) { origBSection.style.display = 'block'; document.getElementById('origTimestampsB').innerHTML = origHtmlB; }
            }
        }

        function buildComparisonTable(reqId, pa, pb) {
            let html = '<table class="comparison-table"><thead><tr>' +
                '<th>Detail</th>' +
                '<th class="path-header">Path A — Smart Processed</th>' +
                '<th class="path-header">Path B — Resemble Enhanced</th>' +
                '</tr></thead><tbody>';

            const rows = [
                ['Output SNR', pa.output_snr_db + ' dB', pb.output_snr_db + ' dB'],
                ['Duration (processed)', pa.duration_seconds + 's', pb.duration_seconds + 's'],
                ['Processing Time', pa.processing_time_seconds + 's', pb.processing_time_seconds + 's'],
                ['Character Count', pa.char_count, pb.char_count],
                ['Segments', pa.segments.length, pb.segments.length],
                ['Audio',
                    '<audio controls src="/audio-preprocess/audio/' + reqId + '/' + pa.audio_file + '"></audio>',
                    '<audio controls src="/audio-preprocess/audio/' + reqId + '/' + pb.audio_file + '"></audio>'],
                ['Transcription',
                    '<div class="transcription-cell">' + (escapeHtml(pa.transcription) || '<em>No speech detected</em>') + '</div>',
                    '<div class="transcription-cell">' + (escapeHtml(pb.transcription) || '<em>No speech detected</em>') + '</div>'],
            ];

            const diarA = buildDiarizedTranscript(pa.segments);
            const diarB = buildDiarizedTranscript(pb.segments);
            if (diarA || diarB) {
                rows.push(['Diarized Transcript',
                    '<div class="transcription-cell">' + (diarA || '<em>No speaker labels</em>') + '</div>',
                    '<div class="transcription-cell">' + (diarB || '<em>No speaker labels</em>') + '</div>']);
            }

            if (!pb.resemble_available) {
                rows.splice(0, 0, ['Resemble Enhance',
                    '<span class="badge badge-skipped">N/A</span>',
                    '<span class="badge badge-triggered">Not installed — fallback copy used</span>']);
            }

            rows.forEach(([label, valA, valB]) => {
                html += '<tr><td>' + label + '</td><td class="path-a-col">' + valA + '</td><td class="path-b-col">' + valB + '</td></tr>';
            });
            html += '</tbody></table>';
            return html;
        }

        function buildSinglePathTable(reqId, pathData, pathName, resembleAvailable) {
            let html = '<table><thead><tr><th style="width:30%">Detail</th><th>Value</th></tr></thead><tbody>';

            if (resembleAvailable === false) {
                html += '<tr><td>Resemble Enhance</td><td><span class="badge badge-triggered">Not installed — fallback copy used</span></td></tr>';
            }

            const rows = [
                ['Output SNR', pathData.output_snr_db + ' dB'],
                ['Duration (processed)', pathData.duration_seconds + 's'],
                ['Processing Time', pathData.processing_time_seconds + 's'],
                ['Character Count', pathData.char_count],
                ['Segments', pathData.segments.length],
                ['Audio', pathData.audio_file ? '<audio controls src="/audio-preprocess/audio/' + reqId + '/' + pathData.audio_file + '"></audio>' : 'N/A'],
                ['Transcription', '<div class="transcription-cell">' + (escapeHtml(pathData.transcription) || '<em>No speech detected</em>') + '</div>'],
            ];

            const diarText = buildDiarizedTranscript(pathData.segments);
            if (diarText) {
                rows.push(['Diarized Transcript', '<div class="transcription-cell">' + diarText + '</div>']);
            }

            rows.forEach(([label, val]) => {
                html += '<tr><td>' + label + '</td><td>' + val + '</td></tr>';
            });
            html += '</tbody></table>';
            return html;
        }

        const SPEAKER_COLORS = ['#60a5fa', '#4ade80', '#fbbf24', '#f87171', '#a78bfa', '#fb923c', '#2dd4bf', '#f472b6', '#818cf8', '#34d399'];

        function getSpeakerColor(speaker) {
            if (!speaker || speaker === 'UNKNOWN') return '#888';
            // Extract speaker number from "SPEAKER_00", "SPEAKER_01", etc.
            const match = speaker.match(/\\d+$/);
            if (match) {
                const idx = parseInt(match[0]) % SPEAKER_COLORS.length;
                return SPEAKER_COLORS[idx];
            }
            return SPEAKER_COLORS[0];
        }

        function buildDiarizedTranscript(segments) {
            if (!segments || segments.length === 0) return null;
            const hasSpeakers = segments.some(s => s.speaker && s.speaker !== 'UNKNOWN');
            if (!hasSpeakers) return null;
            return segments.map(s => {
                const spk = s.speaker || 'UNKNOWN';
                const color = getSpeakerColor(spk);
                return '<span style="color:' + color + '; font-weight:600;">[' + escapeHtml(spk) + ']</span> ' + escapeHtml(s.text || '');
            }).join('\\n');
        }

        function toggleWords(segId, btn) {
            const rows = document.querySelectorAll('.word-row.seg-' + segId);
            const isVisible = rows.length > 0 && rows[0].classList.contains('visible');
            rows.forEach(r => {
                if (isVisible) {
                    r.classList.remove('visible');
                } else {
                    r.classList.add('visible');
                }
            });
            btn.innerHTML = isVisible ? '&#9654;' : '&#9660;';
        }

        function toggleOrigWords(segId, btn) {
            const rows = document.querySelectorAll('.orig-word-row.orig-seg-' + segId);
            const isVisible = rows.length > 0 && rows[0].classList.contains('visible');
            rows.forEach(r => {
                if (isVisible) {
                    r.classList.remove('visible');
                } else {
                    r.classList.add('visible');
                }
            });
            btn.innerHTML = isVisible ? '&#9654;' : '&#9660;';
        }

        function renderOriginalTimestampsTable(segments) {
            if (!segments || segments.length === 0) return '';
            const hasOrig = segments.some(s => s.original_start !== undefined);
            if (!hasOrig) return '';

            const hasSpeakerCol = segments.some(s => s.speaker);
            const hasWords = segments.some(s => s.words && s.words.length > 0);
            const hasTranslation = segments.some(s => s.translation);

            let html = '<table class="segments-table"><thead><tr>' +
                '<th style="width:5%">#</th>' +
                (hasSpeakerCol ? '<th style="width:9%">Speaker</th>' : '') +
                '<th style="width:9%">Orig Start</th>' +
                '<th style="width:9%">Orig End</th>' +
                '<th style="width:7%">Duration</th>' +
                '<th>Text</th>' +
                (hasTranslation ? '<th>Translation (English)</th>' : '') +
                '</tr></thead><tbody>';

            segments.forEach((s, i) => {
                const origStart = s.original_start != null ? s.original_start : s.start;
                const origEnd = s.original_end != null ? s.original_end : s.end;
                const dur = (origEnd - origStart).toFixed(2);
                const spkColor = hasSpeakerCol ? getSpeakerColor(s.speaker) : '';
                const hasSegWords = hasWords && s.words && s.words.length > 0;
                const hasSegTransWords = s.translation_words && s.translation_words.length > 0;

                html += '<tr>' +
                    '<td class="seg-meta">' +
                    ((hasSegWords || hasSegTransWords) ? '<button class="expand-btn" onclick="toggleOrigWords(' + i + ', this)" title="Show original word timestamps">&#9654;</button> ' : '') +
                    (i + 1) + '</td>' +
                    (hasSpeakerCol ? '<td style="color:' + spkColor + '; font-weight:600; font-size:11px;">' + (s.speaker || '-') + '</td>' : '') +
                    '<td class="seg-time">' + formatTime(origStart) + '</td>' +
                    '<td class="seg-time">' + formatTime(origEnd) + '</td>' +
                    '<td class="seg-meta">' + dur + 's</td>' +
                    '<td class="seg-text">' + escapeHtml(s.text) + '</td>' +
                    (hasTranslation ? '<td class="seg-text" style="color:#a8d5ff;">' + escapeHtml(s.translation || '-') + '</td>' : '') +
                    '</tr>';

                if (hasSegWords) {
                    s.words.forEach((w, wi) => {
                        const wOrigStart = w.original_start != null ? w.original_start : w.start;
                        const wOrigEnd = w.original_end != null ? w.original_end : w.end;
                        const wDur = (wOrigEnd - wOrigStart).toFixed(3);
                        const scoreColor = w.score >= 0.7 ? '#4ade80' : (w.score >= 0.4 ? '#fbbf24' : '#f87171');
                        html += '<tr class="orig-word-row orig-seg-' + i + '">' +
                            '<td style="text-align:right; color:#555;">' + (wi + 1) + '</td>' +
                            (hasSpeakerCol ? '<td></td>' : '') +
                            '<td class="seg-time" style="opacity:0.7;">' + formatTime(wOrigStart) + '</td>' +
                            '<td class="seg-time" style="opacity:0.7;">' + formatTime(wOrigEnd) + '</td>' +
                            '<td class="seg-meta">' + wDur + 's</td>' +
                            '<td style="color:#c0c0c0; font-style:italic;">' + escapeHtml(w.word) + ' <span style="color:' + scoreColor + '; font-size:10px;">(score: ' + w.score + ')</span></td>' +
                            (hasTranslation ? '<td></td>' : '') +
                            '</tr>';
                    });
                }

                // Translation word sub-rows with original timestamps
                if (hasSegTransWords) {
                    const totalCols = 6 + (hasSpeakerCol ? 1 : 0) + (hasTranslation ? 1 : 0);
                    html += '<tr class="orig-word-row orig-seg-' + i + '">' +
                        '<td colspan="' + totalCols + '" style="background:#1a2030 !important; color:#60a5fa; font-size:10px; padding:4px 10px !important; font-weight:600; border-top:1px solid #2a3040 !important;">Translation Words (English)</td>' +
                        '</tr>';
                    s.translation_words.forEach((w, wi) => {
                        const wOrigStart = w.original_start != null ? w.original_start : w.start;
                        const wOrigEnd = w.original_end != null ? w.original_end : w.end;
                        const wDur = (wOrigEnd - wOrigStart).toFixed(3);
                        const scoreColor = w.score >= 0.7 ? '#4ade80' : (w.score >= 0.4 ? '#fbbf24' : '#f87171');
                        html += '<tr class="orig-word-row orig-seg-' + i + '">' +
                            '<td style="text-align:right; color:#555;">' + (wi + 1) + '</td>' +
                            (hasSpeakerCol ? '<td></td>' : '') +
                            '<td class="seg-time" style="opacity:0.7; color:#60a5fa;">' + formatTime(wOrigStart) + '</td>' +
                            '<td class="seg-time" style="opacity:0.7; color:#60a5fa;">' + formatTime(wOrigEnd) + '</td>' +
                            '<td class="seg-meta">' + wDur + 's</td>' +
                            '<td></td>' +
                            (hasTranslation ? '<td style="color:#a8d5ff; font-style:italic;">' + escapeHtml(w.word) + ' <span style="color:' + scoreColor + '; font-size:10px;">(score: ' + w.score + ')</span></td>' : '') +
                            '</tr>';
                    });
                }
            });

            html += '</tbody></table>';
            html += '<div style="margin-top:8px;font-size:11px;color:#666;">' +
                'Timestamps remapped to original audio (before silence removal). ' +
                ((hasWords || hasTranslation) ? '<strong>&#9654;</strong> Click the arrow to expand word-level timestamps.' : '') +
                (hasTranslation ? ' <strong style="color:#60a5fa;">Blue timestamps</strong> = translation word positions.' : '') +
                '</div>';
            return html;
        }

        function renderSegmentsTable(segments) {
            if (!segments || segments.length === 0) {
                return '<p style="color:#666;padding:8px;">No segments returned by ASR.</p>';
            }

            const hasLangCol = segments.some(s => s.detected_language);
            const hasSpeakerCol = segments.some(s => s.speaker);
            const hasWords = segments.some(s => s.words && s.words.length > 0);
            const hasTranslation = segments.some(s => s.translation);
            const hasExpandable = hasWords || hasTranslation;

            let html = '<table class="segments-table"><thead><tr>' +
                '<th style="width:5%">#</th>' +
                (hasSpeakerCol ? '<th style="width:9%">Speaker</th>' : '') +
                '<th style="width:7%">Start</th>' +
                '<th style="width:7%">End</th>' +
                '<th style="width:7%">Duration</th>' +
                (hasLangCol ? '<th style="width:6%">Lang</th>' : '') +
                '<th>Text</th>' +
                (hasTranslation ? '<th>Translation (English)</th>' : '') +
                '<th style="width:9%">Avg LogProb</th>' +
                '<th style="width:9%">No Speech</th>' +
                '<th style="width:9%">Compress Ratio</th>' +
                '</tr></thead><tbody>';

            segments.forEach((s, i) => {
                const dur = (s.end - s.start).toFixed(2);
                const logColor = s.avg_logprob < -1.0 ? '#f87171' : (s.avg_logprob < -0.5 ? '#fbbf24' : '#4ade80');
                const nspColor = s.no_speech_prob > 0.5 ? '#f87171' : '#4ade80';
                const crColor = s.compression_ratio > 2.4 ? '#f87171' : '#4ade80';
                const spkColor = hasSpeakerCol ? getSpeakerColor(s.speaker) : '';
                const hasSegWords = s.words && s.words.length > 0;
                const hasSegTransWords = s.translation_words && s.translation_words.length > 0;

                html += '<tr>' +
                    '<td class="seg-meta">' +
                    ((hasSegWords || hasSegTransWords) ? '<button class="expand-btn" onclick="toggleWords(' + i + ', this)" title="Show word timestamps">&#9654;</button> ' : '') +
                    (i + 1) + '</td>' +
                    (hasSpeakerCol ? '<td style="color:' + spkColor + '; font-weight:600; font-size:11px;">' + (s.speaker || '-') + '</td>' : '') +
                    '<td class="seg-time">' + formatTime(s.start) + '</td>' +
                    '<td class="seg-time">' + formatTime(s.end) + '</td>' +
                    '<td class="seg-meta">' + dur + 's</td>' +
                    (hasLangCol ? '<td class="seg-meta" style="color:#60a5fa;">' + (s.detected_language || '-') + '</td>' : '') +
                    '<td class="seg-text">' + escapeHtml(s.text) + '</td>' +
                    (hasTranslation ? '<td class="seg-text" style="color:#a8d5ff;">' + escapeHtml(s.translation || '-') + '</td>' : '') +
                    '<td style="color:' + logColor + '">' + s.avg_logprob + '</td>' +
                    '<td style="color:' + nspColor + '">' + s.no_speech_prob + '</td>' +
                    '<td style="color:' + crColor + '">' + s.compression_ratio + '</td>' +
                    '</tr>';

                // Word sub-rows (hidden by default, toggled by expand button)
                if (hasSegWords) {
                    s.words.forEach((w, wi) => {
                        const wDur = (w.end - w.start).toFixed(3);
                        const scoreColor = w.score >= 0.7 ? '#4ade80' : (w.score >= 0.4 ? '#fbbf24' : '#f87171');
                        html += '<tr class="word-row seg-' + i + '">' +
                            '<td style="text-align:right; color:#555;">' + (wi + 1) + '</td>' +
                            (hasSpeakerCol ? '<td></td>' : '') +
                            '<td class="seg-time" style="opacity:0.7;">' + formatTime(w.start) + '</td>' +
                            '<td class="seg-time" style="opacity:0.7;">' + formatTime(w.end) + '</td>' +
                            '<td class="seg-meta">' + wDur + 's</td>' +
                            (hasLangCol ? '<td></td>' : '') +
                            '<td style="color:#c0c0c0; font-style:italic;">' + escapeHtml(w.word) + '</td>' +
                            (hasTranslation ? '<td></td>' : '') +
                            '<td colspan="3" style="color:' + scoreColor + '; font-size:11px;">Score: ' + w.score + '</td>' +
                            '</tr>';
                    });
                }

                // Translation word sub-rows (shown after original words)
                if (hasSegTransWords) {
                    const totalCols = 6 + (hasSpeakerCol ? 1 : 0) + (hasLangCol ? 1 : 0) + (hasTranslation ? 1 : 0) + 3;
                    html += '<tr class="word-row seg-' + i + '">' +
                        '<td colspan="' + totalCols + '" style="background:#1a2030 !important; color:#60a5fa; font-size:10px; padding:4px 10px !important; font-weight:600; border-top:1px solid #2a3040 !important;">Translation Words (English)</td>' +
                        '</tr>';
                    s.translation_words.forEach((w, wi) => {
                        const wDur = (w.end - w.start).toFixed(3);
                        const scoreColor = w.score >= 0.7 ? '#4ade80' : (w.score >= 0.4 ? '#fbbf24' : '#f87171');
                        html += '<tr class="word-row seg-' + i + '">' +
                            '<td style="text-align:right; color:#555;">' + (wi + 1) + '</td>' +
                            (hasSpeakerCol ? '<td></td>' : '') +
                            '<td class="seg-time" style="opacity:0.7; color:#60a5fa;">' + formatTime(w.start) + '</td>' +
                            '<td class="seg-time" style="opacity:0.7; color:#60a5fa;">' + formatTime(w.end) + '</td>' +
                            '<td class="seg-meta">' + wDur + 's</td>' +
                            (hasLangCol ? '<td></td>' : '') +
                            '<td></td>' +
                            (hasTranslation ? '<td style="color:#a8d5ff; font-style:italic;">' + escapeHtml(w.word) + '</td>' : '') +
                            '<td colspan="3" style="color:' + scoreColor + '; font-size:11px;">Score: ' + w.score + '</td>' +
                            '</tr>';
                    });
                }
            });

            html += '</tbody></table>';

            html += '<div style="margin-top:8px;font-size:11px;color:#666;">' +
                (hasExpandable ? '<strong>&#9654;</strong> Click the arrow to expand/collapse word-level timestamps. ' : '') +
                (hasWords ? '<strong>Score:</strong> Alignment confidence (<span style="color:#4ade80;">green</span> &ge; 0.7 = good, <span style="color:#fbbf24;">yellow</span> 0.4–0.7 = moderate, <span style="color:#f87171;">red</span> &lt; 0.4 = poor). ' : '') +
                (hasSpeakerCol ? '<strong>Speaker:</strong> Identified by Pyannote diarization (color-coded per speaker). ' : '') +
                '<strong>Avg LogProb:</strong> Whisper\\'s confidence (closer to 0 = more confident, < -1.0 = low confidence, shown in red). ' +
                '<strong>No Speech Prob:</strong> Probability the segment is not speech (> 0.5 = likely silence/noise, shown in red). ' +
                '<strong>Compression Ratio:</strong> Text repetitiveness (> 2.4 = likely hallucination, shown in red).' +
                (hasLangCol ? ' <strong>Lang:</strong> Detected language for this segment (multilingual mode).' : '') +
                (hasTranslation ? ' <strong>Translation:</strong> English translation via IndicTrans2 or Whisper translate (blue text).' : '') +
                '</div>';

            return html;
        }

        function renderIntelligence(intel) {
            const ext = intel.extraction || {};
            const cm = intel.coaching_metrics || {};
            let html = '';

            // ── Call Summary ──
            const summary = ext.summary || {};
            if (summary.one_line || summary.detailed) {
                html += '<div class="section-title">Call Summary</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:16px; margin-bottom:16px;">';
                if (summary.one_line) {
                    html += '<div style="font-size:15px; font-weight:600; color:#fff; margin-bottom:8px;">' + escapeHtml(summary.one_line) + '</div>';
                }
                if (summary.detailed) {
                    html += '<div style="font-size:13px; color:#ccc; line-height:1.6;">' + escapeHtml(summary.detailed) + '</div>';
                }
                if (summary.key_topics && summary.key_topics.length > 0) {
                    html += '<div style="margin-top:10px;">';
                    summary.key_topics.forEach(t => {
                        html += '<span style="display:inline-block; background:#22252f; color:#4a9eff; padding:3px 10px; border-radius:12px; font-size:11px; margin:3px 4px 3px 0;">' + escapeHtml(t) + '</span>';
                    });
                    html += '</div>';
                }
                html += '</div>';
            }

            // ── Coaching Metrics ──
            if (cm && Object.keys(cm).length > 0) {
                html += '<div class="section-title">Coaching Metrics</div>';
                html += '<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:16px;">';
                const tr = cm.talk_ratio || {};
                const trColor = tr.assessment === 'optimal' ? '#4ade80' : (tr.assessment === 'rep_dominant' ? '#fbbf24' : '#60a5fa');
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Talk Ratio (Rep)</div>' +
                    '<div style="font-size:24px; font-weight:700; color:' + trColor + ';">' + (tr.rep_percentage || 0) + '%</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">' + (tr.assessment || '-').replace('_', ' ') + '</div>' +
                    '</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Longest Monologue</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.longest_monologue_seconds || 0) + 's</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">' + escapeHtml(cm.longest_monologue_speaker || '-') + '</div>' +
                    '</div>';
                const qc = cm.question_count || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Questions Asked</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#4a9eff;">' + ((qc.rep || 0) + (qc.customer || 0)) + '</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">Rep: ' + (qc.rep || 0) + ' | Customer: ' + (qc.customer || 0) + '</div>' +
                    '</div>';
                const fc = cm.filler_word_count || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Filler Words (Rep)</div>' +
                    '<div style="font-size:24px; font-weight:700; color:' + ((fc.rep || 0) > 20 ? '#f87171' : '#4ade80') + ';">' + (fc.rep || 0) + '</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">Customer: ' + (fc.customer || 0) + '</div>' +
                    '</div>';
                html += '</div>';
                // Second row of metrics
                html += '<div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-bottom:16px;">';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Interruptions</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.interruption_count || 0) + '</div>' +
                    '</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Avg Response Time</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.average_response_time_seconds || 0) + 's</div>' +
                    '</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Silence</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.silence_percentage || 0) + '%</div>' +
                    '</div>';
                html += '</div>';
            }

            // ── Deal Health + Coaching Scorecard + Methodology (top cards) ──
            const dh = ext.deal_health || {};
            const cs = ext.coaching_scorecard || {};
            const ms = ext.methodology_scores || {};
            if (dh.score !== undefined || cs.grade || ms.meddic) {
                html += '<div class="section-title">Scoring</div>';
                html += '<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:16px;">';
                // Deal Health
                const dhColor = dh.label === 'healthy' ? '#4ade80' : (dh.label === 'at_risk' ? '#fbbf24' : '#f87171');
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Deal Health</div>' +
                    '<div style="font-size:28px; font-weight:700; color:' + dhColor + ';">' + (dh.score != null ? dh.score : '-') + '</div>' +
                    '<div style="font-size:10px; color:' + dhColor + '; margin-top:4px; text-transform:uppercase; letter-spacing:1px;">' + (dh.label || '-').replace('_', ' ') + '</div>' +
                    '</div>';
                // Coaching Grade
                const gradeColor = cs.grade === 'A' ? '#4ade80' : (cs.grade === 'B' ? '#60a5fa' : (cs.grade === 'C' ? '#fbbf24' : '#f87171'));
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Coaching Grade</div>' +
                    '<div style="font-size:36px; font-weight:700; color:' + gradeColor + ';">' + (cs.grade || '-') + '</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">Score: ' + (cs.score || 0) + '/100</div>' +
                    '</div>';
                // MEDDIC
                const meddic = ms.meddic || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">MEDDIC</div>' +
                    '<div style="font-size:28px; font-weight:700; color:#fff;">' + (meddic.total_score || 0) + '<span style="font-size:14px; color:#666;">/6</span></div>' +
                    '<div style="margin-top:6px; font-size:10px; text-align:left; padding:0 4px;">';
                ['metrics', 'economic_buyer', 'decision_criteria', 'decision_process', 'identify_pain', 'champion'].forEach(k => {
                    const label = k.replace(/_/g, ' ');
                    const checked = meddic[k];
                    html += '<div style="color:' + (checked ? '#4ade80' : '#555') + ';">' + (checked ? '&#10003;' : '&#10007;') + ' ' + label + '</div>';
                });
                html += '</div></div>';
                // BANT
                const bant = ms.bant || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">BANT</div>' +
                    '<div style="font-size:28px; font-weight:700; color:#fff;">' + (bant.total_score || 0) + '<span style="font-size:14px; color:#666;">/4</span></div>' +
                    '<div style="margin-top:6px; font-size:10px; text-align:left; padding:0 4px;">';
                ['budget', 'authority', 'need', 'timeline'].forEach(k => {
                    const checked = bant[k];
                    html += '<div style="color:' + (checked ? '#4ade80' : '#555') + ';">' + (checked ? '&#10003;' : '&#10007;') + ' ' + k + '</div>';
                });
                html += '</div></div>';
                html += '</div>';
            }

            // ── Sentiment ──
            const sentiment = ext.overall_sentiment || {};
            if (sentiment.overall) {
                html += '<div class="section-title">Sentiment</div>';
                html += '<table><thead><tr><th>Aspect</th><th>Value</th></tr></thead><tbody>';
                const sentColor = s => s === 'positive' ? '#4ade80' : (s === 'negative' ? '#f87171' : '#fbbf24');
                html += '<tr><td>Overall</td><td style="color:' + sentColor(sentiment.overall) + '; font-weight:600;">' + (sentiment.overall || '-') + '</td></tr>';
                html += '<tr><td>Customer Net</td><td style="color:' + sentColor(sentiment.customer_net) + ';">' + (sentiment.customer_net || '-') + '</td></tr>';
                html += '<tr><td>Rep Net</td><td style="color:' + sentColor(sentiment.rep_net) + ';">' + (sentiment.rep_net || '-') + '</td></tr>';
                html += '<tr><td>Trajectory</td><td>' + (sentiment.trajectory || '-').replace(/_/g, ' ') + '</td></tr>';
                if (sentiment.notable_shifts && sentiment.notable_shifts.length > 0) {
                    html += '<tr><td>Notable Shifts</td><td>';
                    sentiment.notable_shifts.forEach(s => {
                        html += '<div style="font-size:12px; margin-bottom:4px;">' +
                            (s.from_sentiment || '?') + ' &rarr; ' + (s.to_sentiment || '?') +
                            (s.trigger ? ' — <em>' + escapeHtml(s.trigger) + '</em>' : '') + '</div>';
                    });
                    html += '</td></tr>';
                }
                html += '</tbody></table>';
            }

            // ── Competitors ──
            const competitors = ext.competitors || [];
            if (competitors.length > 0) {
                html += '<div class="section-title">Competitors Mentioned (' + competitors.length + ')</div>';
                html += '<table><thead><tr><th>Name</th><th>Sentiment</th><th>Context</th><th>Quote</th></tr></thead><tbody>';
                competitors.forEach(c => {
                    const cs = c.sentiment === 'positive' ? '#4ade80' : (c.sentiment === 'negative' ? '#f87171' : '#fbbf24');
                    html += '<tr><td style="font-weight:600;">' + escapeHtml(c.name) + '</td>' +
                        '<td style="color:' + cs + ';">' + (c.sentiment || '-') + '</td>' +
                        '<td>' + escapeHtml(c.context || '-') + '</td>' +
                        '<td style="font-style:italic; color:#999; font-size:12px;">' + escapeHtml(c.quote || '-') + '</td></tr>';
                });
                html += '</tbody></table>';
            }

            // ── Pain Points ──
            const painPoints = ext.pain_points || [];
            if (painPoints.length > 0) {
                html += '<div class="section-title">Pain Points (' + painPoints.length + ')</div>';
                html += '<table><thead><tr><th>Description</th><th>Business Impact</th><th>Quote</th></tr></thead><tbody>';
                painPoints.forEach(p => {
                    html += '<tr><td>' + escapeHtml(p.description) + '</td>' +
                        '<td>' + escapeHtml(p.business_impact || '-') + '</td>' +
                        '<td style="font-style:italic; color:#999; font-size:12px;">' + escapeHtml(p.quote || '-') + '</td></tr>';
                });
                html += '</tbody></table>';
            }

            // ── Objections ──
            const objections = ext.objections || [];
            if (objections.length > 0) {
                html += '<div class="section-title">Objections (' + objections.length + ')</div>';
                html += '<table><thead><tr><th>Type</th><th>Description</th><th>Handled?</th><th>Rep Response</th></tr></thead><tbody>';
                objections.forEach(o => {
                    html += '<tr><td><span class="badge" style="background:#22252f; color:#e0e0e0;">' + escapeHtml(o.type || 'other') + '</span></td>' +
                        '<td>' + escapeHtml(o.description) + '</td>' +
                        '<td>' + (o.handled ? '<span style="color:#4ade80;">Yes</span>' : '<span style="color:#f87171;">No</span>') + '</td>' +
                        '<td style="font-size:12px; color:#999;">' + escapeHtml(o.rep_response || '-') + '</td></tr>';
                });
                html += '</tbody></table>';
            }

            // ── Next Steps ──
            const nextSteps = ext.next_steps || [];
            if (nextSteps.length > 0) {
                html += '<div class="section-title">Next Steps (' + nextSteps.length + ')</div>';
                html += '<table><thead><tr><th>Description</th><th>Owner</th><th>Type</th><th>Due Date</th></tr></thead><tbody>';
                nextSteps.forEach(ns => {
                    html += '<tr><td>' + escapeHtml(ns.description) + '</td>' +
                        '<td>' + (ns.owner || '-') + '</td>' +
                        '<td>' + (ns.type || '-').replace(/_/g, ' ') + '</td>' +
                        '<td>' + escapeHtml(ns.due_date || '-') + '</td></tr>';
                });
                html += '</tbody></table>';
            }

            // ── Budget & Decision Making ──
            const budget = ext.budget || {};
            const decision = ext.decision_making || {};
            if (budget.discussed || decision.decision_maker) {
                html += '<div class="section-title">Budget & Decision Making</div>';
                html += '<div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px;">';
                // Budget
                html += '<div><table><thead><tr><th colspan="2">Budget</th></tr></thead><tbody>';
                html += '<tr><td>Discussed</td><td>' + (budget.discussed ? '<span style="color:#4ade80;">Yes</span>' : 'No') + '</td></tr>';
                if (budget.amount) html += '<tr><td>Amount</td><td>' + (budget.currency || '') + ' ' + budget.amount + '</td></tr>';
                html += '<tr><td>Pricing Presented</td><td>' + (budget.pricing_presented ? 'Yes' : 'No') + '</td></tr>';
                html += '<tr><td>Discount Requested</td><td>' + (budget.discount_requested ? 'Yes' : 'No') + '</td></tr>';
                html += '<tr><td>ROI Discussed</td><td>' + (budget.roi_discussed ? 'Yes' : 'No') + '</td></tr>';
                if (budget.quote) html += '<tr><td>Quote</td><td style="font-style:italic; color:#999; font-size:12px;">' + escapeHtml(budget.quote) + '</td></tr>';
                html += '</tbody></table></div>';
                // Decision Making
                html += '<div><table><thead><tr><th colspan="2">Decision Making</th></tr></thead><tbody>';
                html += '<tr><td>Decision Maker</td><td>' + escapeHtml(decision.decision_maker || '-') + '</td></tr>';
                if (decision.buying_committee && decision.buying_committee.length > 0) {
                    html += '<tr><td>Buying Committee</td><td>' + decision.buying_committee.map(escapeHtml).join(', ') + '</td></tr>';
                }
                html += '<tr><td>Decision Process</td><td>' + escapeHtml(decision.decision_process || '-') + '</td></tr>';
                html += '<tr><td>Timeline</td><td>' + escapeHtml(decision.decision_timeline || '-') + '</td></tr>';
                html += '<tr><td>Champion</td><td>' + (decision.champion_identified ? '<span style="color:#4ade80;">' + escapeHtml(decision.champion_name || 'Yes') + '</span>' : 'Not identified') + '</td></tr>';
                html += '</tbody></table></div>';
                html += '</div>';
            }

            // ── Deal Stage ──
            const stage = ext.deal_stage || {};
            if (stage.current_stage && stage.current_stage !== 'unknown') {
                html += '<div class="section-title">Deal Stage</div>';
                html += '<table><thead><tr><th>Detail</th><th>Value</th></tr></thead><tbody>';
                html += '<tr><td>Current Stage</td><td style="font-weight:600; color:#4a9eff; text-transform:capitalize;">' + (stage.current_stage || '-') + '</td></tr>';
                html += '<tr><td>Confidence</td><td>' + ((stage.confidence || 0) * 100).toFixed(0) + '%</td></tr>';
                if (stage.stage_signals && stage.stage_signals.length > 0) {
                    html += '<tr><td>Stage Signals</td><td>' + stage.stage_signals.map(escapeHtml).join('<br>') + '</td></tr>';
                }
                if (stage.risk_signals && stage.risk_signals.length > 0) {
                    html += '<tr><td style="color:#f87171;">Risk Signals</td><td style="color:#f87171;">' + stage.risk_signals.map(escapeHtml).join('<br>') + '</td></tr>';
                }
                html += '</tbody></table>';
            }

            // ── Processing Info ──
            html += '<div style="margin-top:12px; font-size:11px; color:#555;">Intelligence extraction time: ' + intel.processing_time_seconds + 's</div>';

            return html;
        }

        function renderMemoirSummary(intel) {
            const ext = intel.extraction || {};
            const cm = intel.coaching_metrics || {};
            let html = '';

            const confBadge = (conf) => {
                const colors = {
                    definitive: { bg: '#1a3a1a', text: '#4ade80', icon: '&#9679;' },
                    indicative: { bg: '#3a3a1a', text: '#fbbf24', icon: '&#9679;' },
                    inferred: { bg: '#1a2a3a', text: '#60a5fa', icon: '&#9679;' },
                    ambiguous: { bg: '#22252f', text: '#888', icon: '&#9675;' },
                };
                const c = colors[conf] || colors.ambiguous;
                return '<span style="display:inline-block; background:' + c.bg + '; color:' + c.text +
                    '; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600;">' +
                    c.icon + ' ' + (conf || 'ambiguous') + '</span>';
            };

            // ── Overall Confidence Score ──
            const score = ext.overall_confidence_score || 0;
            const scoreColor = score >= 70 ? '#4ade80' : (score >= 40 ? '#fbbf24' : '#f87171');
            html += '<div class="section-title" style="display:flex; align-items:center; justify-content:space-between;">Memoir Call Summary' +
                '<span style="font-size:14px; color:' + scoreColor + '; font-weight:700;">Confidence: ' + score + '/100</span></div>';

            // ── Call Metadata ──
            const meta = ext.call_metadata || {};
            if (meta.call_type || (meta.participants && meta.participants.length > 0)) {
                html += '<div style="background:#1a1d27; border-radius:8px; padding:12px 16px; margin-bottom:12px; display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:#888;">';
                if (meta.call_type) html += '<span>Type: <span style="color:#e0e0e0;">' + escapeHtml(meta.call_type) + '</span></span>';
                if (meta.duration_minutes) html += '<span>Duration: <span style="color:#e0e0e0;">' + meta.duration_minutes + ' min</span></span>';
                if (meta.participants && meta.participants.length > 0) {
                    const pNames = meta.participants.map(p => escapeHtml((p.name || 'Unknown') + (p.title ? ' (' + p.title + ')' : '') + (p.role ? ' [' + p.role + ']' : ''))).join(', ');
                    html += '<span>Participants: <span style="color:#e0e0e0;">' + pNames + '</span></span>';
                }
                html += '</div>';
            }

            // ── Discovery Fields ──
            const df = ext.discovery_fields || {};
            html += '<div style="background:#1a1d27; border-radius:8px; padding:16px; margin-bottom:16px;">';
            html += '<div style="font-size:13px; font-weight:600; color:#fff; margin-bottom:12px;">Discovery Fields</div>';

            const fieldConfigs = [
                { key: 'budget', icon: '&#128176;', label: 'Budget', valueFn: f => f.value },
                { key: 'authority', icon: '&#128100;', label: 'Authority', valueFn: f => {
                    const parts = [];
                    if (f.decision_maker) parts.push(f.decision_maker + (f.decision_maker_title ? ' (' + f.decision_maker_title + ')' : ''));
                    if (f.champions && f.champions.length) parts.push('Champions: ' + f.champions.join(', '));
                    if (f.blockers && f.blockers.length) parts.push('Blockers: ' + f.blockers.join(', '));
                    if (f.approval_process) parts.push('Process: ' + f.approval_process);
                    return parts.join(' | ') || null;
                }},
                { key: 'need', icon: '&#127919;', label: 'Need', valueFn: f => {
                    const parts = [];
                    if (f.primary_pain) parts.push(f.primary_pain);
                    if (f.secondary_pains && f.secondary_pains.length) parts.push('Also: ' + f.secondary_pains.join(', '));
                    if (f.current_workaround) parts.push('Workaround: ' + f.current_workaround);
                    return parts.join(' | ') || null;
                }},
                { key: 'timeline', icon: '&#128197;', label: 'Timeline', valueFn: f => {
                    const parts = [];
                    if (f.target_date) parts.push(f.target_date);
                    if (f.urgency && f.urgency !== 'none') parts.push('(' + f.urgency + ')');
                    if (f.driving_event) parts.push('— ' + f.driving_event);
                    return parts.join(' ') || null;
                }},
                { key: 'tech_stack', icon: '&#128295;', label: 'Tech Stack', valueFn: f => {
                    const parts = [];
                    if (f.current_tools && f.current_tools.length) parts.push(f.current_tools.join(', '));
                    if (f.pain_with_current) parts.push('Pain: ' + f.pain_with_current);
                    if (f.integrations_needed && f.integrations_needed.length) parts.push('Needs: ' + f.integrations_needed.join(', '));
                    return parts.join(' | ') || null;
                }},
                { key: 'team', icon: '&#128101;', label: 'Team', valueFn: f => {
                    const parts = [];
                    if (f.size) parts.push(f.size);
                    if (f.roles && f.roles.length) parts.push('(' + f.roles.join(', ') + ')');
                    if (f.locations) parts.push(f.locations);
                    return parts.join(' ') || null;
                }},
                { key: 'competition', icon: '&#9876;', label: 'Competition', valueFn: f => {
                    const parts = [];
                    if (f.competitors_evaluating && f.competitors_evaluating.length) parts.push(f.competitors_evaluating.join(', '));
                    if (f.competitor_sentiment && f.competitor_sentiment.length) {
                        f.competitor_sentiment.forEach(cs => { parts.push(cs.competitor + ': ' + cs.sentiment); });
                    }
                    return parts.join(' | ') || null;
                }},
            ];

            fieldConfigs.forEach(fc => {
                const field = df[fc.key] || {};
                const value = fc.valueFn(field);
                const conf = field.confidence || 'ambiguous';
                const displayValue = value || '[not discussed]';
                const valueColor = value ? '#e0e0e0' : '#555';
                html += '<div style="display:flex; align-items:flex-start; padding:8px 0; border-bottom:1px solid #22252f;">';
                html += '<span style="width:28px; text-align:center; font-size:16px;">' + fc.icon + '</span>';
                html += '<span style="width:100px; font-size:12px; color:#888; font-weight:600; padding-top:2px;">' + fc.label + '</span>';
                html += '<span style="flex:1; font-size:13px; color:' + valueColor + ';">' + escapeHtml(displayValue) + '</span>';
                html += '<span style="margin-left:8px;">' + confBadge(conf) + '</span>';
                html += '</div>';
                // Source quote if present
                if (field.source_quote && field.source_quote !== 'Not discussed in this call') {
                    html += '<div style="margin-left:128px; font-size:11px; color:#666; font-style:italic; padding:2px 0 4px 0;">' +
                        '"' + escapeHtml(field.source_quote) + '"' +
                        (field.source_speaker ? ' — ' + escapeHtml(field.source_speaker) : '') +
                        (field.source_timestamp_approx ? ' [' + escapeHtml(field.source_timestamp_approx) + ']' : '') +
                        '</div>';
                }
            });
            html += '</div>';

            // ── Narrative Summary ──
            if (ext.narrative_summary) {
                html += '<div style="background:#1a1d27; border-radius:8px; padding:16px; margin-bottom:16px;">';
                html += '<div style="font-size:13px; font-weight:600; color:#fff; margin-bottom:8px;">Narrative Summary</div>';
                html += '<div style="font-size:13px; color:#ccc; line-height:1.7; white-space:pre-wrap;">' + escapeHtml(ext.narrative_summary) + '</div>';
                html += '</div>';
            }

            // ── Coaching Metrics (shared with Phase 7) ──
            if (cm && Object.keys(cm).length > 0) {
                html += '<div class="section-title">Coaching Metrics</div>';
                html += '<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:16px;">';
                const tr = cm.talk_ratio || {};
                const trColor = tr.assessment === 'optimal' ? '#4ade80' : (tr.assessment === 'rep_dominant' ? '#fbbf24' : '#60a5fa');
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Talk Ratio (Rep)</div>' +
                    '<div style="font-size:24px; font-weight:700; color:' + trColor + ';">' + (tr.rep_percentage || 0) + '%</div>' +
                    '<div style="font-size:10px; color:#666; margin-top:4px;">' + (tr.assessment || '-').replace('_', ' ') + '</div></div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Longest Monologue</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.longest_monologue_seconds || 0) + 's</div></div>';
                const qc = cm.question_count || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Questions</div>' +
                    '<div style="font-size:24px; font-weight:700; color:#4a9eff;">' + ((qc.rep || 0) + (qc.customer || 0)) + '</div></div>';
                const fc = cm.filler_word_count || {};
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                    '<div style="font-size:11px; color:#888; margin-bottom:6px;">Filler Words</div>' +
                    '<div style="font-size:24px; font-weight:700; color:' + ((fc.rep || 0) > 20 ? '#f87171' : '#4ade80') + ';">' + (fc.rep || 0) + '</div></div>';
                html += '</div>';
            }

            // ── Action Items ──
            const actions = ext.action_items || {};
            const repActions = actions.rep_actions || [];
            const prospectCommits = actions.prospect_commitments || [];
            if (repActions.length > 0 || prospectCommits.length > 0) {
                html += '<div class="section-title">Action Items</div>';
                html += '<div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px;">';
                // Rep actions
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px;">';
                html += '<div style="font-size:12px; font-weight:600; color:#4ade80; margin-bottom:8px;">Your Actions</div>';
                if (repActions.length > 0) {
                    repActions.forEach(a => {
                        const prioColor = a.priority === 'high' ? '#f87171' : (a.priority === 'medium' ? '#fbbf24' : '#888');
                        html += '<div style="display:flex; align-items:flex-start; margin-bottom:6px; font-size:13px;">' +
                            '<span style="color:#4ade80; margin-right:8px;">&#8226;</span>' +
                            '<span style="flex:1; color:#e0e0e0;">' + escapeHtml(a.action) +
                            (a.due_date ? ' <span style="color:#888;">— ' + escapeHtml(a.due_date) + '</span>' : '') +
                            '</span>' +
                            '<span style="color:' + prioColor + '; font-size:10px; font-weight:600; text-transform:uppercase;">' + (a.priority || '') + '</span>' +
                            '</div>';
                    });
                } else {
                    html += '<div style="color:#555; font-size:12px;">None identified</div>';
                }
                html += '</div>';
                // Prospect commitments
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px;">';
                html += '<div style="font-size:12px; font-weight:600; color:#60a5fa; margin-bottom:8px;">Prospect Committed</div>';
                if (prospectCommits.length > 0) {
                    prospectCommits.forEach(c => {
                        html += '<div style="display:flex; align-items:flex-start; margin-bottom:6px; font-size:13px;">' +
                            '<span style="color:#60a5fa; margin-right:8px;">&#8226;</span>' +
                            '<span style="flex:1; color:#e0e0e0;">' + escapeHtml(c.action) +
                            (c.owner ? ' — <span style="color:#888;">' + escapeHtml(c.owner) + '</span>' : '') +
                            '</span></div>';
                    });
                } else {
                    html += '<div style="color:#555; font-size:12px;">None identified</div>';
                }
                html += '</div></div>';
            }

            // ── Next Steps ──
            const ns = ext.next_steps || {};
            if (ns.next_meeting || ns.next_milestone || ns.suggested_deal_stage) {
                html += '<div class="section-title">Next Steps</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; margin-bottom:16px;">';
                if (ns.next_meeting) html += '<div style="margin-bottom:6px; font-size:13px;"><span style="color:#888; font-weight:600;">Next Meeting:</span> <span style="color:#e0e0e0;">' + escapeHtml(ns.next_meeting) + '</span></div>';
                if (ns.next_milestone) html += '<div style="margin-bottom:6px; font-size:13px;"><span style="color:#888; font-weight:600;">Next Milestone:</span> <span style="color:#e0e0e0;">' + escapeHtml(ns.next_milestone) + '</span></div>';
                if (ns.suggested_deal_stage) html += '<div style="font-size:13px;"><span style="color:#888; font-weight:600;">Suggested Stage:</span> <span style="color:#4a9eff; font-weight:600;">' + escapeHtml(ns.suggested_deal_stage) + '</span></div>';
                html += '</div>';
            }

            // ── Signals ──
            const signals = ext.signals || {};
            const buying = signals.buying_signals || [];
            const risk = signals.risk_signals || [];
            if (buying.length > 0 || risk.length > 0) {
                html += '<div class="section-title">Signals</div>';
                html += '<div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px;">';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px;">';
                html += '<div style="font-size:12px; font-weight:600; color:#4ade80; margin-bottom:8px;">Buying Signals</div>';
                if (buying.length > 0) {
                    buying.forEach(s => {
                        html += '<div style="font-size:12px; color:#ccc; margin-bottom:4px;">"' + escapeHtml(s.signal) + '"' +
                            (s.speaker ? ' — <span style="color:#888;">' + escapeHtml(s.speaker) + '</span>' : '') + '</div>';
                    });
                } else {
                    html += '<div style="color:#555; font-size:12px;">None detected</div>';
                }
                html += '</div>';
                html += '<div style="background:#1a1d27; border-radius:8px; padding:14px;">';
                html += '<div style="font-size:12px; font-weight:600; color:#f87171; margin-bottom:8px;">Risk Signals</div>';
                if (risk.length > 0) {
                    risk.forEach(s => {
                        html += '<div style="font-size:12px; color:#ccc; margin-bottom:4px;">"' + escapeHtml(s.signal) + '"' +
                            (s.speaker ? ' — <span style="color:#888;">' + escapeHtml(s.speaker) + '</span>' : '') + '</div>';
                    });
                } else {
                    html += '<div style="color:#555; font-size:12px;">None detected</div>';
                }
                html += '</div></div>';
            }

            // ── Disclaimer ──
            const disc = ext.disclaimer || {};
            const ambiguous = disc.ambiguous_topics || [];
            if (ambiguous.length > 0 || disc.confidential_content_detected || disc.transcript_quality_issues) {
                html += '<div class="section-title">Disclaimer</div>';
                html += '<div style="background:#2a2020; border:1px solid #533; border-radius:8px; padding:14px; margin-bottom:16px;">';
                if (ambiguous.length > 0) {
                    html += '<div style="font-size:12px; color:#fbbf24; margin-bottom:6px;">The following topics could not be confidently captured:</div>';
                    ambiguous.forEach(t => {
                        html += '<div style="font-size:12px; color:#ccc; margin-left:12px; margin-bottom:3px;">&#8226; <strong>' + escapeHtml(t.topic) + '</strong>: ' + escapeHtml(t.reason) + '</div>';
                    });
                }
                if (disc.confidential_content_detected) {
                    html += '<div style="font-size:12px; color:#f87171; margin-top:8px;">&#9888; Confidential remarks were detected and excluded from this summary.</div>';
                }
                if (disc.transcript_quality_issues) {
                    html += '<div style="font-size:12px; color:#fbbf24; margin-top:8px;">&#9888; Transcript quality: ' + escapeHtml(disc.transcript_quality_issues) + '</div>';
                }
                html += '</div>';
            }

            // ── Processing Info ──
            html += '<div style="margin-top:12px; font-size:11px; color:#555;">Memoir extraction time: ' + (intel.processing_time_seconds || 0) + 's</div>';

            return html;
        }

        function formatTime(seconds) {
            const m = Math.floor(seconds / 60);
            const s = (seconds % 60).toFixed(2);
            return m > 0 ? m + ':' + s.padStart(5, '0') : s + 's';
        }

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // ── Unicode font loading for PDF (Tamil, Hindi, etc.) ──
        // Full Noto Sans fonts (Latin + Indic glyphs) served locally from FastAPI
        const FONT_URLS = {
            'NotoSansTamil': '/audio-preprocess/fonts/NotoSansTamil-Regular.ttf',
            'NotoSansDevanagari': '/audio-preprocess/fonts/NotoSansDevanagari-Regular.ttf',
        };
        let _fontCache = {};

        async function loadFontAsBase64(url) {
            if (_fontCache[url]) return _fontCache[url];
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`Font fetch failed: ${resp.status} ${resp.statusText}`);
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binary = '';
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            const b64 = btoa(binary);
            _fontCache[url] = b64;
            return b64;
        }

        function hasIndicChars(text) {
            if (!text) return false;
            // Tamil: U+0B80-U+0BFF, Devanagari: U+0900-U+097F
            return /[\\u0B80-\\u0BFF\\u0900-\\u097F]/.test(text);
        }

        function hasTamil(text) {
            return text && /[\\u0B80-\\u0BFF]/.test(text);
        }

        function hasDevanagari(text) {
            return text && /[\\u0900-\\u097F]/.test(text);
        }

        let _loadedFonts = { tamil: false, devanagari: false };

        async function registerFonts(doc) {
            // Load Tamil font
            try {
                const tamilB64 = await loadFontAsBase64(FONT_URLS.NotoSansTamil);
                doc.addFileToVFS('NotoSansTamil-Regular.ttf', tamilB64);
                doc.addFont('NotoSansTamil-Regular.ttf', 'NotoSansTamil', 'normal');
                _loadedFonts.tamil = true;
            } catch (e) {
                console.warn('Failed to load Tamil font:', e);
            }
            // Load Devanagari font
            try {
                const devaB64 = await loadFontAsBase64(FONT_URLS.NotoSansDevanagari);
                doc.addFileToVFS('NotoSansDevanagari-Regular.ttf', devaB64);
                doc.addFont('NotoSansDevanagari-Regular.ttf', 'NotoSansDevanagari', 'normal');
                _loadedFonts.devanagari = true;
            } catch (e) {
                console.warn('Failed to load Devanagari font:', e);
            }
        }

        function setFontForText(doc, text) {
            // Pick the right font based on script detected in text
            if (_loadedFonts.tamil && hasTamil(text)) {
                doc.setFont('NotoSansTamil', 'normal');
            } else if (_loadedFonts.devanagari && hasDevanagari(text)) {
                doc.setFont('NotoSansDevanagari', 'normal');
            } else {
                doc.setFont('helvetica', 'normal');
            }
        }

        // autoTable hook: set font per cell based on content
        function indicFontHook(data) {
            if (data.section === 'body' && data.cell && data.cell.raw) {
                const txt = String(data.cell.raw);
                if (_loadedFonts.tamil && hasTamil(txt)) {
                    data.cell.styles.font = 'NotoSansTamil';
                } else if (_loadedFonts.devanagari && hasDevanagari(txt)) {
                    data.cell.styles.font = 'NotoSansDevanagari';
                }
            }
        }

        async function downloadPDF() {
            if (!lastResultData) return;
            const btn = document.getElementById('downloadPdf');
            btn.disabled = true;
            btn.textContent = 'Loading fonts...';

            const { jsPDF } = window.jspdf;
            const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });

            // Register Unicode fonts for Tamil/Hindi support
            await registerFonts(doc);

            const data = lastResultData;
            const a = data.input_analysis;
            const mode = data.processing_mode || 'path_a';
            let y = 15;

            btn.textContent = 'Generating PDF...';

            // Title
            doc.setFont('helvetica', 'bold');
            doc.setFontSize(18);
            doc.setTextColor(40, 40, 40);
            doc.text('Audio Preprocessing Report', 14, y);
            y += 8;
            doc.setFont('helvetica', 'normal');
            doc.setFontSize(10);
            doc.setTextColor(120, 120, 120);
            doc.text('Generated: ' + new Date().toLocaleString(), 14, y);
            doc.text('Mode: ' + (mode === 'both' ? 'A/B Comparison' : mode === 'path_a' ? 'Path A — Smart Processed' : 'Path B — Resemble Enhanced'), 14, y + 5);
            y += 14;

            // Input Analysis
            doc.setFont('helvetica', 'bold');
            doc.setFontSize(13);
            doc.setTextColor(40, 40, 40);
            doc.text('Input Audio Analysis', 14, y);
            y += 3;
            doc.autoTable({
                startY: y,
                head: [['Metric', 'Value', 'Status']],
                body: [
                    ['File', a.file_name, '-'],
                    ['Sample Rate', a.sample_rate + ' Hz', a.needs_bandwidth_extension ? 'BW Extension triggered' : 'OK'],
                    ['Duration', a.duration_seconds + 's', '-'],
                    ['Channels', String(a.channels), '-'],
                    ['Bit Depth', a.bit_depth, '-'],
                    ['Peak Amplitude', String(a.peak_amplitude), '-'],
                    ['RMS Amplitude', String(a.rms_amplitude), '-'],
                    ['SNR', a.snr_db + ' dB', a.needs_denoising ? 'Denoise + OA triggered' : 'OK'],
                    ['Clipping', a.clipping_percentage + '%', a.needs_declipping ? 'Declipping triggered' : 'OK'],
                ],
                theme: 'grid',
                headStyles: { fillColor: [74, 158, 255], fontSize: 9 },
                bodyStyles: { fontSize: 8 },
                margin: { left: 14, right: 14 },
                didParseCell: indicFontHook,
            });
            y = doc.lastAutoTable.finalY + 10;

            // Processing Steps
            doc.setFont('helvetica', 'bold');
            doc.setFontSize(13);
            doc.text('Processing Steps', 14, y);
            y += 3;
            doc.autoTable({
                startY: y,
                head: [['Step', 'Status', 'Reason']],
                body: data.steps.map(s => [s.step, s.status.toUpperCase(), s.reason]),
                theme: 'grid',
                headStyles: { fillColor: [74, 158, 255], fontSize: 9 },
                bodyStyles: { fontSize: 7 },
                columnStyles: {
                    0: { cellWidth: 35 },
                    1: { cellWidth: 18 },
                    2: { cellWidth: 'auto' },
                },
                margin: { left: 14, right: 14 },
                didParseCell: indicFontHook,
            });
            y = doc.lastAutoTable.finalY + 10;

            // Vocabulary Corrections (5a — if any)
            if (data.vocabulary && data.vocabulary.enabled) {
                const allCorr = [...(data.vocabulary.corrections_a || []), ...(data.vocabulary.corrections_b || [])];
                if (y > 250) { doc.addPage(); y = 15; }
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(13);
                doc.text('Vocabulary Corrections — 5a (' + data.vocabulary.name + ')', 14, y);
                y += 3;
                if (allCorr.length > 0) {
                    doc.autoTable({
                        startY: y,
                        head: [['Original', 'Corrected To', 'Type', 'Field', 'Match Score']],
                        body: allCorr.map(c => [c.original_text, c.corrected_text, c.correction_type, c.field || 'text', String(c.match_score || '-')]),
                        theme: 'grid',
                        headStyles: { fillColor: [74, 158, 255], fontSize: 9 },
                        bodyStyles: { fontSize: 8 },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 10;
                } else {
                    doc.setFont('helvetica', 'normal');
                    doc.setFontSize(9);
                    doc.text('No corrections applied.', 14, y + 5);
                    y += 14;
                }
            }

            // LLM Context Corrections (5b — if any)
            if (data.llm_correction && data.llm_correction.enabled) {
                const allLlm = [...(data.llm_correction.corrections_a || []), ...(data.llm_correction.corrections_b || [])];
                const applied = allLlm.filter(c => c.correction_type === 'llm_context');
                const rejected = allLlm.filter(c => c.correction_type === 'llm_rejected');
                if (y > 250) { doc.addPage(); y = 15; }
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(13);
                doc.text('LLM Context Corrections — 5b (Ollama)', 14, y);
                y += 3;
                doc.setFont('helvetica', 'normal');
                doc.setFontSize(9);
                doc.text('Candidates: ' + allLlm.length + ' | Applied: ' + applied.length + ' | Rejected: ' + rejected.length + ' | Time: ' + data.llm_correction.processing_time_seconds + 's', 14, y + 3);
                y += 8;
                if (applied.length > 0) {
                    doc.autoTable({
                        startY: y,
                        head: [['Original', 'Corrected To', 'Field', 'Confidence', 'LLM Reasoning']],
                        body: applied.map(c => [c.original_text, c.corrected_text, c.field || 'text', String(c.confidence_before || '-'), c.llm_reasoning || '']),
                        theme: 'grid',
                        headStyles: { fillColor: [74, 222, 128], fontSize: 9 },
                        bodyStyles: { fontSize: 7 },
                        columnStyles: { 4: { cellWidth: 60 } },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }
                if (rejected.length > 0) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'italic');
                    doc.setFontSize(9);
                    doc.text('Rejected (phonetic verification failed):', 14, y);
                    y += 2;
                    doc.autoTable({
                        startY: y,
                        head: [['Original', 'LLM Suggested', 'Field', 'Reasoning']],
                        body: rejected.map(c => [c.original_text, c.corrected_text, c.field || 'text', c.llm_reasoning || '']),
                        theme: 'grid',
                        headStyles: { fillColor: [248, 113, 113], fontSize: 9 },
                        bodyStyles: { fontSize: 7 },
                        columnStyles: { 3: { cellWidth: 60 } },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }
                if (applied.length === 0 && rejected.length === 0) {
                    doc.text('No phonetic candidates found.', 14, y);
                    y += 10;
                }
            }

            // Intelligence / Memoir (if enabled)
            if (data.intelligence && data.intelligence.enabled && data.intelligence.extraction && Object.keys(data.intelligence.extraction).length > 0) {
                const ext = data.intelligence.extraction;
                const cm = data.intelligence.coaching_metrics || {};
                const ppType = data.intelligence.post_processing_type || 'phase7';

                if (y > 250) { doc.addPage(); y = 15; }
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(14);
                doc.text(ppType === 'memoir' ? 'Memoir Call Summary' : 'Call Intelligence (Phase 7)', 14, y);
                y += 8;

                // Memoir-specific PDF sections
                if (ppType === 'memoir') {
                    // Discovery Fields
                    const df = ext.discovery_fields || {};
                    const dfRows = [];
                    const fieldNames = {budget: 'Budget', authority: 'Authority', need: 'Need', timeline: 'Timeline', tech_stack: 'Tech Stack', team: 'Team', competition: 'Competition'};
                    Object.entries(fieldNames).forEach(([key, label]) => {
                        const f = df[key] || {};
                        let val = '';
                        if (key === 'budget') val = f.value || '-';
                        else if (key === 'authority') val = f.decision_maker || '-';
                        else if (key === 'need') val = f.primary_pain || '-';
                        else if (key === 'timeline') val = (f.target_date || '-') + (f.urgency && f.urgency !== 'none' ? ' (' + f.urgency + ')' : '');
                        else if (key === 'tech_stack') val = (f.current_tools || []).join(', ') || '-';
                        else if (key === 'team') val = f.size || '-';
                        else if (key === 'competition') val = (f.competitors_evaluating || []).join(', ') || '-';
                        dfRows.push([label, val, (f.confidence || 'ambiguous'), (f.source_quote || '-').substring(0, 80)]);
                    });
                    doc.autoTable({
                        startY: y,
                        head: [['Field', 'Value', 'Confidence', 'Source']],
                        body: dfRows,
                        theme: 'grid',
                        headStyles: { fillColor: [74, 158, 255], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                    });
                    y = doc.lastAutoTable.finalY + 6;

                    // Narrative
                    if (ext.narrative_summary) {
                        if (y > 250) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Narrative Summary:', 14, y);
                        y += 4;
                        doc.setFont('helvetica', 'normal');
                        doc.setFontSize(8);
                        const narLines = doc.splitTextToSize(ext.narrative_summary, 180);
                        narLines.forEach(l => { if (y > 280) { doc.addPage(); y = 15; } doc.text(l, 14, y); y += 3.5; });
                        y += 6;
                    }

                    // Action Items
                    const actions = ext.action_items || {};
                    const repActs = actions.rep_actions || [];
                    const prosCommits = actions.prospect_commitments || [];
                    if (repActs.length > 0 || prosCommits.length > 0) {
                        if (y > 250) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Action Items:', 14, y);
                        y += 3;
                        const aiRows = [];
                        repActs.forEach(a => aiRows.push(['Rep', a.action, a.priority || '-', a.due_date || '-']));
                        prosCommits.forEach(c => aiRows.push(['Prospect (' + (c.owner || '-') + ')', c.action, '-', c.due_date || '-']));
                        doc.autoTable({
                            startY: y,
                            head: [['Owner', 'Action', 'Priority', 'Due']],
                            body: aiRows,
                            theme: 'grid',
                            headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                            bodyStyles: { fontSize: 7.5 },
                            margin: { left: 14, right: 14 },
                        });
                        y = doc.lastAutoTable.finalY + 6;
                    }

                    // Coaching Metrics (shared)
                    if (cm && Object.keys(cm).length > 0) {
                        if (y > 250) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Coaching Metrics:', 14, y);
                        y += 3;
                        const tr = cm.talk_ratio || {};
                        const qc = cm.question_count || {};
                        const fc = cm.filler_word_count || {};
                        doc.autoTable({
                            startY: y,
                            head: [['Metric', 'Value']],
                            body: [
                                ['Talk Ratio (Rep)', (tr.rep_percentage || 0) + '% (' + (tr.assessment || '-').replace('_', ' ') + ')'],
                                ['Longest Monologue', (cm.longest_monologue_seconds || 0) + 's'],
                                ['Questions (Rep/Customer)', (qc.rep || 0) + ' / ' + (qc.customer || 0)],
                                ['Filler Words (Rep)', String(fc.rep || 0)],
                                ['Interruptions', String(cm.interruption_count || 0)],
                            ],
                            theme: 'grid',
                            headStyles: { fillColor: [74, 158, 255], fontSize: 8 },
                            bodyStyles: { fontSize: 7.5 },
                            margin: { left: 14, right: 14 },
                        });
                        y = doc.lastAutoTable.finalY + 6;
                    }

                    // Next Steps
                    const mNs = ext.next_steps || {};
                    if (mNs.next_meeting || mNs.next_milestone || mNs.suggested_deal_stage) {
                        if (y > 260) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Next Steps:', 14, y);
                        y += 3;
                        const nsRows = [];
                        if (mNs.next_meeting) nsRows.push(['Next Meeting', mNs.next_meeting]);
                        if (mNs.next_milestone) nsRows.push(['Next Milestone', mNs.next_milestone]);
                        if (mNs.suggested_deal_stage) nsRows.push(['Suggested Stage', mNs.suggested_deal_stage]);
                        doc.autoTable({
                            startY: y,
                            head: [['Item', 'Details']],
                            body: nsRows,
                            theme: 'grid',
                            headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                            bodyStyles: { fontSize: 7.5 },
                            margin: { left: 14, right: 14 },
                        });
                        y = doc.lastAutoTable.finalY + 6;
                    }

                    // Signals
                    const mSignals = ext.signals || {};
                    const mBuying = mSignals.buying_signals || [];
                    const mRisk = mSignals.risk_signals || [];
                    if (mBuying.length > 0 || mRisk.length > 0) {
                        if (y > 250) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Signals:', 14, y);
                        y += 3;
                        const sigRows = [];
                        mBuying.forEach(s => sigRows.push(['Buying', s.signal, s.speaker || '-']));
                        mRisk.forEach(s => sigRows.push(['Risk', s.signal, s.speaker || '-']));
                        doc.autoTable({
                            startY: y,
                            head: [['Type', 'Signal', 'Speaker']],
                            body: sigRows,
                            theme: 'grid',
                            headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                            bodyStyles: { fontSize: 7.5 },
                            margin: { left: 14, right: 14 },
                        });
                        y = doc.lastAutoTable.finalY + 6;
                    }

                    // Disclaimer
                    const mDisc = ext.disclaimer || {};
                    const mAmbig = mDisc.ambiguous_topics || [];
                    if (mAmbig.length > 0 || mDisc.confidential_content_detected || mDisc.transcript_quality_issues) {
                        if (y > 260) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Disclaimer:', 14, y);
                        y += 4;
                        doc.setFont('helvetica', 'normal');
                        doc.setFontSize(8);
                        if (mAmbig.length > 0) {
                            mAmbig.forEach(t => { if (y > 280) { doc.addPage(); y = 15; } doc.text('- ' + t.topic + ': ' + t.reason, 18, y); y += 3.5; });
                        }
                        if (mDisc.confidential_content_detected) { doc.text('* Confidential remarks detected and excluded.', 18, y); y += 3.5; }
                        if (mDisc.transcript_quality_issues) { doc.text('* Quality: ' + mDisc.transcript_quality_issues, 18, y); y += 3.5; }
                        y += 4;
                    }

                    // Overall confidence
                    if (y > 270) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Overall Confidence: ' + (ext.overall_confidence_score || 0) + '/100', 14, y);
                    y += 8;
                }

                // Phase 7-specific PDF sections (only when not memoir)
                if (ppType !== 'memoir') {

                // Summary
                const summary = ext.summary || {};
                if (summary.one_line) {
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Summary:', 14, y);
                    y += 5;
                    doc.setFont('helvetica', 'normal');
                    doc.setFontSize(9);
                    setFontForText(doc, summary.one_line);
                    const sumLines = doc.splitTextToSize(summary.one_line, 180);
                    sumLines.forEach(l => { doc.text(l, 14, y); y += 4; });
                    if (summary.detailed) {
                        y += 2;
                        setFontForText(doc, summary.detailed);
                        doc.setFontSize(8);
                        const detLines = doc.splitTextToSize(summary.detailed, 180);
                        detLines.forEach(l => { if (y > 280) { doc.addPage(); y = 15; } doc.text(l, 14, y); y += 3.5; });
                    }
                    y += 6;
                    doc.setFont('helvetica', 'normal');
                }

                // Coaching Metrics table
                if (cm && Object.keys(cm).length > 0) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Coaching Metrics:', 14, y);
                    y += 3;
                    const tr = cm.talk_ratio || {};
                    const qc = cm.question_count || {};
                    const fc = cm.filler_word_count || {};
                    doc.autoTable({
                        startY: y,
                        head: [['Metric', 'Value']],
                        body: [
                            ['Talk Ratio (Rep)', (tr.rep_percentage || 0) + '% (' + (tr.assessment || '-').replace('_', ' ') + ')'],
                            ['Longest Monologue', (cm.longest_monologue_seconds || 0) + 's (' + (cm.longest_monologue_speaker || '-') + ')'],
                            ['Questions (Rep/Customer)', (qc.rep || 0) + ' / ' + (qc.customer || 0)],
                            ['Filler Words (Rep)', String(fc.rep || 0)],
                            ['Interruptions', String(cm.interruption_count || 0)],
                            ['Avg Response Time', (cm.average_response_time_seconds || 0) + 's'],
                            ['Silence', (cm.silence_percentage || 0) + '%'],
                        ],
                        theme: 'grid',
                        headStyles: { fillColor: [74, 158, 255], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                // Scoring summary table
                const dh = ext.deal_health || {};
                const csc = ext.coaching_scorecard || {};
                const ms = ext.methodology_scores || {};
                if (dh.score !== undefined || csc.grade) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Scoring:', 14, y);
                    y += 3;
                    doc.autoTable({
                        startY: y,
                        head: [['Metric', 'Score', 'Details']],
                        body: [
                            ['Deal Health', (dh.score != null ? dh.score + '/100' : '-'), (dh.label || '-').replace('_', ' ')],
                            ['Coaching Grade', csc.grade || '-', 'Score: ' + (csc.score || 0) + '/100'],
                            ['MEDDIC', (ms.meddic ? ms.meddic.total_score : 0) + '/6', 'M:' + (ms.meddic?.metrics ? 'Y' : 'N') + ' E:' + (ms.meddic?.economic_buyer ? 'Y' : 'N') + ' D:' + (ms.meddic?.decision_criteria ? 'Y' : 'N') + ' D:' + (ms.meddic?.decision_process ? 'Y' : 'N') + ' I:' + (ms.meddic?.identify_pain ? 'Y' : 'N') + ' C:' + (ms.meddic?.champion ? 'Y' : 'N')],
                            ['BANT', (ms.bant ? ms.bant.total_score : 0) + '/4', 'B:' + (ms.bant?.budget ? 'Y' : 'N') + ' A:' + (ms.bant?.authority ? 'Y' : 'N') + ' N:' + (ms.bant?.need ? 'Y' : 'N') + ' T:' + (ms.bant?.timeline ? 'Y' : 'N')],
                        ],
                        theme: 'grid',
                        headStyles: { fillColor: [74, 158, 255], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                // Competitors
                const competitors = ext.competitors || [];
                if (competitors.length > 0) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Competitors (' + competitors.length + '):', 14, y);
                    y += 3;
                    doc.autoTable({
                        startY: y,
                        head: [['Name', 'Sentiment', 'Context']],
                        body: competitors.map(c => [c.name, c.sentiment || '-', c.context || '-']),
                        theme: 'grid',
                        headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                // Objections
                const objections = ext.objections || [];
                if (objections.length > 0) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Objections (' + objections.length + '):', 14, y);
                    y += 3;
                    doc.autoTable({
                        startY: y,
                        head: [['Type', 'Description', 'Handled?', 'Rep Response']],
                        body: objections.map(o => [o.type || 'other', o.description, o.handled ? 'Yes' : 'No', o.rep_response || '-']),
                        theme: 'grid',
                        headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                        bodyStyles: { fontSize: 7 },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                // Next Steps
                const nextSteps = ext.next_steps || [];
                if (nextSteps.length > 0) {
                    if (y > 250) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Next Steps (' + nextSteps.length + '):', 14, y);
                    y += 3;
                    doc.autoTable({
                        startY: y,
                        head: [['Description', 'Owner', 'Type', 'Due Date']],
                        body: nextSteps.map(ns => [ns.description, ns.owner || '-', (ns.type || '-').replace(/_/g, ' '), ns.due_date || '-']),
                        theme: 'grid',
                        headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                // Sentiment
                const sentiment = ext.overall_sentiment || {};
                if (sentiment.overall) {
                    if (y > 260) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Sentiment:', 14, y);
                    y += 3;
                    doc.autoTable({
                        startY: y,
                        head: [['Aspect', 'Value']],
                        body: [
                            ['Overall', sentiment.overall],
                            ['Customer Net', sentiment.customer_net || '-'],
                            ['Rep Net', sentiment.rep_net || '-'],
                            ['Trajectory', (sentiment.trajectory || '-').replace(/_/g, ' ')],
                        ],
                        theme: 'grid',
                        headStyles: { fillColor: [100, 116, 139], fontSize: 8 },
                        bodyStyles: { fontSize: 7.5 },
                        margin: { left: 14, right: 14 },
                    });
                    y = doc.lastAutoTable.finalY + 8;
                }

                } // end if (ppType !== 'memoir')

                y += 4;
            }

            // Results
            function addPathResults(pathData, pathName) {
                if (y > 250) { doc.addPage(); y = 15; }
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(13);
                doc.text(pathName + ' — Results', 14, y);
                y += 3;
                doc.autoTable({
                    startY: y,
                    head: [['Detail', 'Value']],
                    body: [
                        ['Output SNR', pathData.output_snr_db + ' dB'],
                        ['Duration', pathData.duration_seconds + 's'],
                        ['Processing Time', pathData.processing_time_seconds + 's'],
                        ['Character Count', String(pathData.char_count)],
                        ['Segments', String(pathData.segments.length)],
                    ],
                    theme: 'grid',
                    headStyles: { fillColor: [74, 158, 255], fontSize: 9 },
                    bodyStyles: { fontSize: 8 },
                    margin: { left: 14, right: 14 },
                });
                y = doc.lastAutoTable.finalY + 6;

                // Transcription
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(10);
                doc.text('Transcription:', 14, y);
                y += 4;
                doc.setFontSize(8);
                doc.setTextColor(60, 60, 60);
                const transcriptionText = pathData.transcription || '(No speech detected)';
                setFontForText(doc, transcriptionText);
                const lines = doc.splitTextToSize(transcriptionText, 180);
                for (let li = 0; li < lines.length; li++) {
                    if (y + 3.5 > 280) {
                        doc.addPage();
                        y = 15;
                        setFontForText(doc, transcriptionText);
                        doc.setFontSize(8);
                        doc.setTextColor(60, 60, 60);
                    }
                    doc.text(lines[li], 14, y);
                    y += 3.5;
                }
                y += 6;
                doc.setTextColor(40, 40, 40);
                doc.setFont('helvetica', 'normal');

                // Segment timestamps
                if (pathData.segments && pathData.segments.length > 0) {
                    if (y > 240) { doc.addPage(); y = 15; }
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(10);
                    doc.text('Segment Timestamps:', 14, y);
                    y += 3;
                    const segHasSpeaker = pathData.segments.some(s => s.speaker);
                    const segHasTranslation = pathData.segments.some(s => s.translation);
                    let segHead, segColStyles;
                    if (segHasSpeaker && segHasTranslation) {
                        segHead = [['#', 'Speaker', 'Start', 'End', 'Text', 'Translation', 'LogProb', 'NoSpeech', 'CompRatio']];
                        segColStyles = { 0: { cellWidth: 7 }, 1: { cellWidth: 18 }, 2: { cellWidth: 13 }, 3: { cellWidth: 13 }, 4: { cellWidth: 'auto' }, 5: { cellWidth: 'auto' }, 6: { cellWidth: 13 }, 7: { cellWidth: 13 }, 8: { cellWidth: 15 } };
                    } else if (segHasSpeaker) {
                        segHead = [['#', 'Speaker', 'Start', 'End', 'Text', 'LogProb', 'NoSpeech', 'CompRatio']];
                        segColStyles = { 0: { cellWidth: 7 }, 1: { cellWidth: 20 }, 2: { cellWidth: 14 }, 3: { cellWidth: 14 }, 4: { cellWidth: 'auto' }, 5: { cellWidth: 14 }, 6: { cellWidth: 14 }, 7: { cellWidth: 16 } };
                    } else if (segHasTranslation) {
                        segHead = [['#', 'Start', 'End', 'Text', 'Translation', 'LogProb', 'NoSpeech', 'CompRatio']];
                        segColStyles = { 0: { cellWidth: 8 }, 1: { cellWidth: 15 }, 2: { cellWidth: 15 }, 3: { cellWidth: 'auto' }, 4: { cellWidth: 'auto' }, 5: { cellWidth: 15 }, 6: { cellWidth: 15 }, 7: { cellWidth: 17 } };
                    } else {
                        segHead = [['#', 'Start', 'End', 'Text', 'LogProb', 'NoSpeech', 'CompRatio']];
                        segColStyles = { 0: { cellWidth: 8 }, 1: { cellWidth: 16 }, 2: { cellWidth: 16 }, 3: { cellWidth: 'auto' }, 4: { cellWidth: 16 }, 5: { cellWidth: 16 }, 6: { cellWidth: 18 } };
                    }
                    const segBody = pathData.segments.map((s, i) => {
                        const row = [String(i + 1)];
                        if (segHasSpeaker) row.push(s.speaker || '-');
                        row.push(s.start.toFixed(2) + 's', s.end.toFixed(2) + 's', s.text);
                        if (segHasTranslation) row.push(s.translation || '');
                        row.push(String(s.avg_logprob), String(s.no_speech_prob), String(s.compression_ratio));
                        return row;
                    });
                    doc.autoTable({
                        startY: y,
                        head: segHead,
                        body: segBody,
                        theme: 'grid',
                        headStyles: { fillColor: [100, 116, 139], fontSize: 7 },
                        bodyStyles: { fontSize: 6.5 },
                        columnStyles: segColStyles,
                        margin: { left: 14, right: 14 },
                        didParseCell: indicFontHook,
                    });
                    y = doc.lastAutoTable.finalY + 10;

                    // Word-level timestamps in PDF (all expanded)
                    const segmentsWithWords = pathData.segments.filter(s => (s.words && s.words.length > 0) || (s.translation_words && s.translation_words.length > 0));
                    if (segmentsWithWords.length > 0) {
                        if (y > 250) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Word-Level Timestamps:', 14, y);
                        y += 3;

                        const wordBody = [];
                        pathData.segments.forEach((seg, si) => {
                            const hasWords = seg.words && seg.words.length > 0;
                            const hasTransWords = seg.translation_words && seg.translation_words.length > 0;
                            if (!hasWords && !hasTransWords) return;
                            // Segment header row
                            const segPreview = seg.text.length > 60 ? seg.text.substring(0, 60) + '...' : seg.text;
                            wordBody.push([{
                                content: 'Segment ' + (si + 1) + ': ' + segPreview,
                                colSpan: 5,
                                styles: { fillColor: [40, 44, 55], textColor: [180, 180, 180], fontStyle: 'bold', fontSize: 6.5 }
                            }]);
                            // Word rows
                            if (hasWords) {
                                seg.words.forEach((w, wi) => {
                                    wordBody.push([
                                        String(wi + 1),
                                        w.word,
                                        w.start.toFixed(3) + 's',
                                        w.end.toFixed(3) + 's',
                                        String(w.score)
                                    ]);
                                });
                            }
                            // Translation word rows
                            if (hasTransWords) {
                                wordBody.push([{
                                    content: 'Translation Words (English):',
                                    colSpan: 5,
                                    styles: { fillColor: [30, 40, 55], textColor: [168, 213, 255], fontStyle: 'italic', fontSize: 6 }
                                }]);
                                seg.translation_words.forEach((w, wi) => {
                                    wordBody.push([
                                        String(wi + 1),
                                        w.word,
                                        w.start.toFixed(3) + 's',
                                        w.end.toFixed(3) + 's',
                                        String(w.score)
                                    ]);
                                });
                            }
                        });

                        doc.autoTable({
                            startY: y,
                            head: [['#', 'Word', 'Start', 'End', 'Score']],
                            body: wordBody,
                            theme: 'grid',
                            headStyles: { fillColor: [100, 116, 139], fontSize: 7 },
                            bodyStyles: { fontSize: 6 },
                            columnStyles: {
                                0: { cellWidth: 8 },
                                1: { cellWidth: 'auto' },
                                2: { cellWidth: 18 },
                                3: { cellWidth: 18 },
                                4: { cellWidth: 14 },
                            },
                            margin: { left: 14, right: 14 },
                            didParseCell: indicFontHook,
                        });
                        y = doc.lastAutoTable.finalY + 10;
                    }

                    // Original Audio Timestamps in PDF (with word sub-rows)
                    const hasOrigTimestamps = pathData.segments.some(s => s.original_start !== undefined);
                    if (hasOrigTimestamps) {
                        if (y > 240) { doc.addPage(); y = 15; }
                        doc.setFont('helvetica', 'bold');
                        doc.setFontSize(10);
                        doc.text('Original Audio Timestamps (before silence removal):', 14, y);
                        y += 3;

                        const origHasSpeaker = pathData.segments.some(s => s.speaker);
                        const origHasWords = pathData.segments.some(s => s.words && s.words.length > 0);
                        const origHasTranslation = pathData.segments.some(s => s.translation);
                        const origHasTransWords = pathData.segments.some(s => s.translation_words && s.translation_words.length > 0);
                        let origHead, origColStyles;
                        if (origHasSpeaker && origHasTranslation) {
                            origHead = [['#', 'Speaker', 'Orig Start', 'Orig End', 'Duration', 'Text', 'Translation']];
                            origColStyles = { 0: { cellWidth: 7 }, 1: { cellWidth: 18 }, 2: { cellWidth: 20 }, 3: { cellWidth: 20 }, 4: { cellWidth: 16 }, 5: { cellWidth: 'auto' }, 6: { cellWidth: 'auto' } };
                        } else if (origHasSpeaker) {
                            origHead = [['#', 'Speaker', 'Orig Start', 'Orig End', 'Duration', 'Text']];
                            origColStyles = { 0: { cellWidth: 8 }, 1: { cellWidth: 20 }, 2: { cellWidth: 22 }, 3: { cellWidth: 22 }, 4: { cellWidth: 18 }, 5: { cellWidth: 'auto' } };
                        } else if (origHasTranslation) {
                            origHead = [['#', 'Orig Start', 'Orig End', 'Duration', 'Text', 'Translation']];
                            origColStyles = { 0: { cellWidth: 8 }, 1: { cellWidth: 20 }, 2: { cellWidth: 20 }, 3: { cellWidth: 16 }, 4: { cellWidth: 'auto' }, 5: { cellWidth: 'auto' } };
                        } else {
                            origHead = [['#', 'Orig Start', 'Orig End', 'Duration', 'Text']];
                            origColStyles = { 0: { cellWidth: 8 }, 1: { cellWidth: 22 }, 2: { cellWidth: 22 }, 3: { cellWidth: 18 }, 4: { cellWidth: 'auto' } };
                        }
                        const origColCount = origHead[0].length;
                        const origBody = [];
                        pathData.segments.forEach((seg, si) => {
                            const os = seg.original_start != null ? seg.original_start : seg.start;
                            const oe = seg.original_end != null ? seg.original_end : seg.end;
                            const dur = (oe - os).toFixed(2) + 's';
                            const row = [String(si + 1)];
                            if (origHasSpeaker) row.push(seg.speaker || '-');
                            row.push(os.toFixed(2) + 's', oe.toFixed(2) + 's', dur, seg.text);
                            if (origHasTranslation) row.push(seg.translation || '');
                            origBody.push(row);
                            // Word sub-rows with original timestamps
                            if (origHasWords && seg.words && seg.words.length > 0) {
                                seg.words.forEach((w, wi) => {
                                    const wos = w.original_start != null ? w.original_start : w.start;
                                    const woe = w.original_end != null ? w.original_end : w.end;
                                    const wdur = (woe - wos).toFixed(3) + 's';
                                    const wordRow = ['  ' + (wi + 1)];
                                    if (origHasSpeaker) wordRow.push('');
                                    wordRow.push(wos.toFixed(3) + 's', woe.toFixed(3) + 's', wdur, w.word + ' (score: ' + w.score + ')');
                                    if (origHasTranslation) wordRow.push('');
                                    origBody.push(wordRow);
                                });
                            }
                            // Translation word sub-rows with original timestamps
                            if (origHasTransWords && seg.translation_words && seg.translation_words.length > 0) {
                                origBody.push([{
                                    content: '  Translation Words (English):',
                                    colSpan: origColCount,
                                    styles: { fillColor: [30, 40, 55], textColor: [168, 213, 255], fontStyle: 'italic', fontSize: 5.5 }
                                }]);
                                seg.translation_words.forEach((w, wi) => {
                                    const wos = w.original_start != null ? w.original_start : w.start;
                                    const woe = w.original_end != null ? w.original_end : w.end;
                                    const wdur = (woe - wos).toFixed(3) + 's';
                                    const wordRow = ['  ' + (wi + 1)];
                                    if (origHasSpeaker) wordRow.push('');
                                    wordRow.push(wos.toFixed(3) + 's', woe.toFixed(3) + 's', wdur, w.word + ' (score: ' + w.score + ')');
                                    if (origHasTranslation) wordRow.push('');
                                    origBody.push(wordRow);
                                });
                            }
                        });
                        doc.autoTable({
                            startY: y,
                            head: origHead,
                            body: origBody,
                            theme: 'grid',
                            headStyles: { fillColor: [100, 116, 139], fontSize: 7 },
                            bodyStyles: { fontSize: 6.5 },
                            columnStyles: origColStyles,
                            margin: { left: 14, right: 14 },
                            didParseCell: function(data) {
                                indicFontHook(data);
                                // Style word sub-rows with lighter background
                                if (data.section === 'body' && data.row.raw && data.row.raw[0] && String(data.row.raw[0]).startsWith('  ')) {
                                    data.cell.styles.fillColor = [30, 33, 48];
                                    data.cell.styles.textColor = [160, 160, 160];
                                    data.cell.styles.fontSize = 5.5;
                                }
                            },
                        });
                        y = doc.lastAutoTable.finalY + 10;
                    }
                }
            }

            // Diarization summary in PDF
            if (data.diarization && data.diarization.enabled) {
                if (y > 250) { doc.addPage(); y = 15; }
                doc.setFont('helvetica', 'bold');
                doc.setFontSize(13);
                doc.text('Speaker Diarization', 14, y);
                y += 3;
                const d = data.diarization;
                let pdfDiarBody = [
                        ['Speakers Detected', String(d.num_speakers)],
                        ['Method', d.method === 'multichannel' ? 'Multichannel (channel-based)' : 'Pyannote AI'],
                        ['Diarization Segments', String(d.speakers.length)],
                        ['Processing Time', d.processing_time_seconds + 's'],
                    ];
                if (d.verification) {
                    const v = d.verification;
                    pdfDiarBody.push(['Embedding Verification', v.merged_clusters + ' merged, ' + v.segments_reassigned + ' reassigned, ' + v.short_segments_inherited + ' inherited, ' + v.processing_time_seconds + 's']);
                    pdfDiarBody.push(['Clusters', v.original_clusters + ' → ' + v.final_clusters]);
                }
                doc.autoTable({
                    startY: y,
                    head: [['Detail', 'Value']],
                    body: pdfDiarBody,
                    theme: 'grid',
                    headStyles: { fillColor: [74, 158, 255], fontSize: 9 },
                    bodyStyles: { fontSize: 8 },
                    margin: { left: 14, right: 14 },
                });
                y = doc.lastAutoTable.finalY + 10;
            }

            if (mode === 'path_a' || mode === 'both') {
                addPathResults(data.path_a, 'Path A — Smart Processed');
            }
            if (mode === 'path_b' || mode === 'both') {
                addPathResults(data.path_b, 'Path B — Resemble Enhanced');
            }

            // Footer
            doc.setFont('helvetica', 'normal');
            const pageCount = doc.getNumberOfPages();
            for (let i = 1; i <= pageCount; i++) {
                doc.setPage(i);
                doc.setFontSize(8);
                doc.setTextColor(150, 150, 150);
                doc.text('Audio Preprocessing Report — Page ' + i + '/' + pageCount, 14, 290);
            }

            const fileName = (a.file_name || 'audio').replace(/\\.[^.]+$/, '');
            doc.save(fileName + '_preprocess_report.pdf');
            btn.disabled = false;
            btn.textContent = 'Download Results as PDF';
        }
    </script>
</body>
</html>
"""
