"""
HTML UI for the standalone post-processing test page.
Allows running vocabulary correction, LLM correction, and intelligence extraction
on transcript text without re-running the full audio pipeline.
"""


def get_postprocess_page() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Post-Processing Test</title>
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
        .subtitle a { color: #4a9eff; text-decoration: none; }
        .subtitle a:hover { text-decoration: underline; }

        .card {
            background: #1a1d27;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-title {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            margin-bottom: 12px;
        }

        textarea {
            width: 100%;
            background: #0f1117;
            color: #e0e0e0;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 12px;
            font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
            font-size: 13px;
            line-height: 1.5;
            resize: vertical;
        }
        textarea:focus { outline: none; border-color: #4a9eff; }
        textarea::placeholder { color: #555; }

        .parse-info {
            margin-top: 8px;
            font-size: 12px;
            color: #666;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .parse-info .count { color: #4a9eff; }
        .parse-info .error { color: #f87171; }

        .sample-btn {
            background: transparent;
            border: 1px solid #333;
            color: #888;
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
        }
        .sample-btn:hover { border-color: #4a9eff; color: #4a9eff; }

        /* Options */
        .options-section {
            background: #1a1d27;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .option-row {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        .option-row input[type="checkbox"] {
            margin-right: 8px;
            accent-color: #4a9eff;
        }
        .option-label { color: #e0e0e0; font-size: 14px; }
        .nested-option {
            margin-left: 28px;
            margin-top: 6px;
            margin-bottom: 6px;
        }
        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-left: 8px;
        }
        .status-dot.green { background: #4ade80; }
        .status-dot.red { background: #f87171; }
        .status-text {
            font-size: 11px;
            color: #888;
            margin-left: 6px;
        }

        select {
            background: #0f1117;
            color: #e0e0e0;
            border: 1px solid #333;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        }
        select:focus { outline: none; border-color: #4a9eff; }

        .process-btn {
            background: #22c55e;
            color: #fff;
            border: none;
            padding: 14px 48px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            display: block;
            margin: 16px auto;
        }
        .process-btn:hover { background: #16a34a; }
        .process-btn:disabled { background: #555; cursor: not-allowed; }

        /* Progress */
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
            width: 14px;
            height: 14px;
            border: 2px solid #333;
            border-top-color: #4a9eff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Results */
        .section-title {
            font-size: 15px;
            font-weight: 600;
            color: #fff;
            margin: 20px 0 10px 0;
            padding-bottom: 6px;
            border-bottom: 1px solid #22252f;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 16px;
            font-size: 13px;
        }
        th {
            background: #22252f;
            color: #888;
            text-align: left;
            padding: 8px 12px;
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        td {
            padding: 8px 12px;
            border-bottom: 1px solid #1a1d27;
            vertical-align: top;
        }
        tr:hover td { background: #16181f; }

        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
        }

        .correction-applied { color: #4ade80; }
        .correction-rejected { color: #f87171; }

        .transcription-box {
            background: #0f1117;
            border: 1px solid #22252f;
            border-radius: 8px;
            padding: 16px;
            font-size: 14px;
            line-height: 1.8;
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 400px;
            overflow-y: auto;
        }
    </style>
</head>
<body>
    <h1>Post-Processing Test</h1>
    <p class="subtitle">
        Run vocabulary correction, LLM correction, and intelligence extraction on transcript text
        &mdash; without the audio pipeline.
        <a href="/audio-preprocess/">&larr; Back to Main Pipeline</a>
    </p>

    <!-- ═══════ Input Section ═══════ -->
    <div class="card">
        <div class="card-title">Transcript Input</div>
        <textarea id="transcriptInput" rows="14" placeholder="Paste transcript text here...

With speaker labels (from diarized output):
[SPEAKER_00] Hello, welcome to the call.
[SPEAKER_01] Hi, thanks for having me.

Or plain text (from transcription column):
Hello, welcome to the call. Hi, thanks for having me."></textarea>
        <div class="parse-info">
            <span id="parseInfo"></span>
            <button class="sample-btn" onclick="loadSample()">Load Sample</button>
        </div>
    </div>

    <!-- ═══════ Options Section ═══════ -->
    <div class="options-section">
        <div class="card-title">Processing Options</div>

        <!-- Vocabulary Correction -->
        <div class="option-row">
            <input type="checkbox" id="vocabCheck" onchange="toggleVocabOptions()">
            <span class="option-label">Enable Vocabulary Correction</span>
        </div>
        <div id="vocabOptions" style="display:none;">
            <div class="nested-option">
                <label style="font-size:13px; color:#888; margin-bottom:6px; display:block;">Vocabularies:</label>
                <div id="vocabCheckboxes" style="max-height:160px; overflow-y:auto; padding:8px;
                        background:#22252f; border:1px solid #333; border-radius:8px;">
                    <span style="color:#666; font-size:12px;">Loading vocabularies...</span>
                </div>
            </div>
            <div class="nested-option option-row">
                <input type="checkbox" id="llmCheck">
                <span class="option-label">Enable LLM Correction</span>
                <span id="llmDot" class="status-dot red"></span>
                <span id="llmStatus" class="status-text">checking...</span>
            </div>
        </div>

        <!-- Intelligence -->
        <div class="option-row" style="margin-top: 12px;">
            <input type="checkbox" id="intelCheck" onchange="togglePpType()">
            <span class="option-label">Enable Call Intelligence</span>
            <span id="intelDot" class="status-dot red"></span>
            <span id="intelStatus" class="status-text">checking...</span>
        </div>
        <div id="ppTypeRow" class="nested-option" style="display:none; margin-top:6px;">
            <label style="font-size:12px; color:#888; margin-right:8px;">Post-Processing Type:</label>
            <select id="ppTypeSelect" style="background:#0f1117; color:#e0e0e0; border:1px solid #333; border-radius:6px; padding:5px 10px; font-size:13px;">
                <option value="phase7">Phase 7 Intelligence</option>
                <option value="memoir">Memoir Summary</option>
            </select>
        </div>

        <!-- Language -->
        <div class="option-row" style="margin-top: 12px;">
            <label style="font-size:13px; color:#888; margin-right:8px;">Language:</label>
            <select id="langSelect">
                <option value="en">English</option>
                <option value="ta">Tamil</option>
                <option value="hi">Hindi</option>
                <option value="auto">Auto-detect</option>
            </select>
        </div>
    </div>

    <!-- ═══════ Run Button ═══════ -->
    <button class="process-btn" id="runBtn" onclick="runPostProcessing()">Run Post-Processing</button>

    <!-- ═══════ Progress ═══════ -->
    <div class="progress" id="progress">
        <div class="progress-info">
            <span><span class="spinner"></span><span id="progressStep">Processing...</span></span>
            <span id="progressPct" style="font-weight:700; color:#fff;">0%</span>
        </div>
        <div class="progress-bar-bg">
            <div class="progress-bar-fill" id="progressFill"></div>
        </div>
    </div>

    <!-- ═══════ Results ═══════ -->
    <div id="results" style="display:none;">
        <div id="vocabResults"></div>
        <div id="llmResults"></div>
        <div id="intelligenceSection"></div>
        <div id="correctedTranscriptSection"></div>
    </div>

<script>
    // ═══════════════════════════════════════════
    // Input parsing
    // ═══════════════════════════════════════════

    const transcriptInput = document.getElementById('transcriptInput');
    transcriptInput.addEventListener('input', updateParseInfo);

    function parseTranscript(text) {
        if (!text || !text.trim()) return [];
        const lines = text.split('\\n').filter(l => l.trim());
        const segments = [];

        // Detect format: check if any line has [SPEAKER_XX] prefix
        const speakerPattern = /^\\[?(SPEAKER_\\d+)\\]?\\s*(?:\\[([\\d.]+)\\s*-\\s*([\\d.]+)\\])?:?\\s*(.+)$/;
        const hasSpeakers = lines.some(l => speakerPattern.test(l.trim()));

        if (hasSpeakers) {
            // Format 1: Speaker-labeled lines
            let currentTime = 0.0;
            for (const line of lines) {
                const match = line.trim().match(speakerPattern);
                if (match) {
                    const speaker = match[1];
                    const start = match[2] ? parseFloat(match[2]) : currentTime;
                    const words = match[4].trim().split(/\\s+/).length;
                    const duration = words / 2.5; // ~150 wpm
                    const end = match[3] ? parseFloat(match[3]) : (start + duration);
                    segments.push({
                        speaker: speaker,
                        start: start,
                        end: end,
                        text: match[4].trim(),
                        avg_logprob: -0.3,
                        no_speech_prob: 0.1,
                        compression_ratio: 1.5
                    });
                    currentTime = end + 0.5;
                }
            }
        } else {
            // Format 2: Plain text — split by sentences
            const sentences = text.match(/[^.!?]+[.!?]+/g) || [text.trim()];
            let currentTime = 0.0;
            for (const sentence of sentences) {
                const trimmed = sentence.trim();
                if (!trimmed) continue;
                const words = trimmed.split(/\\s+/).length;
                const duration = words / 2.5;
                segments.push({
                    speaker: null,
                    start: currentTime,
                    end: currentTime + duration,
                    text: trimmed,
                    avg_logprob: -0.3,
                    no_speech_prob: 0.1,
                    compression_ratio: 1.5
                });
                currentTime += duration + 0.5;
            }
        }
        return segments;
    }

    function updateParseInfo() {
        const segments = parseTranscript(transcriptInput.value);
        const info = document.getElementById('parseInfo');
        if (segments.length === 0) {
            info.innerHTML = '';
            return;
        }
        const speakers = new Set(segments.map(s => s.speaker).filter(Boolean));
        const duration = Math.max(...segments.map(s => s.end || 0));
        const durStr = duration > 60 ? Math.floor(duration/60) + ':' + (duration%60).toFixed(0).padStart(2,'0') : duration.toFixed(0) + 's';
        info.innerHTML = '<span class="count">' + segments.length + ' segments</span>' +
            (speakers.size > 0 ? ' &middot; ' + speakers.size + ' speakers' : ' &middot; no speaker labels') +
            ' &middot; ~' + durStr + ' duration';
    }

    function loadSample() {
        transcriptInput.value =
`[SPEAKER_00] Hi John, thanks for taking the time today. I'm Sarah from Acme Solutions. I wanted to walk you through how our platform can help with your CRM challenges.
[SPEAKER_01] Sure, thanks Sarah. We've been struggling with our current setup using Salesforce. The reporting is slow and the team finds it hard to track deals properly.
[SPEAKER_00] I hear that a lot. What's the biggest impact on your team's productivity? Can you quantify how much time they spend on manual reporting?
[SPEAKER_01] Our reps spend about 5 hours a week on manual data entry. That's roughly 25% of their selling time lost. We also looked at HubSpot but their pricing was too high for us.
[SPEAKER_00] That's significant. Our platform automates 90% of that data entry. The pricing starts at 50 dollars per user per month. Would that fit within your budget?
[SPEAKER_01] That's a bit more than we planned. We were thinking around 30 dollars per user. But if it really saves that much time, I'd need to discuss with our VP of Sales, Mike Chen.
[SPEAKER_00] Absolutely. How about I set up a demo for you and Mike next week? I can show the ROI calculator and we can discuss volume pricing.
[SPEAKER_01] That sounds great. Send me some available times for Tuesday or Wednesday. We want to make a decision by end of Q1.`;
        updateParseInfo();
    }

    // ═══════════════════════════════════════════
    // Options — load vocabularies & check LLM status
    // ═══════════════════════════════════════════

    function togglePpType() {
        document.getElementById('ppTypeRow').style.display =
            document.getElementById('intelCheck').checked ? 'block' : 'none';
    }

    function toggleVocabOptions() {
        document.getElementById('vocabOptions').style.display =
            document.getElementById('vocabCheck').checked ? 'block' : 'none';
    }

    // Load vocabulary list as checkboxes
    (async function loadVocabs() {
        try {
            const resp = await fetch('/audio-preprocess/vocabulary/list');
            const data = await resp.json();
            const container = document.getElementById('vocabCheckboxes');
            const vocabs = data.vocabularies || [];
            if (vocabs.length === 0) {
                container.innerHTML = '<span style="color:#666; font-size:12px;">No vocabularies found. Add JSON files to the vocabularies/ folder.</span>';
                return;
            }
            container.innerHTML = '';
            vocabs.forEach(v => {
                const label = document.createElement('label');
                label.style.cssText = 'display:flex; align-items:center; padding:4px 0; cursor:pointer; font-size:13px; color:#e0e0e0;';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'vocab-checkbox';
                cb.value = v.name;
                cb.style.cssText = 'margin-right:8px; accent-color:#4a9eff;';
                label.appendChild(cb);
                label.appendChild(document.createTextNode(v.display_name + ' (' + v.entry_count + ' terms)'));
                container.appendChild(label);
            });
        } catch(e) { console.error('Failed to load vocabularies:', e); }
    })();

    // Check LLM status
    (async function checkLLM() {
        try {
            const resp = await fetch('/audio-preprocess/llm-status');
            const data = await resp.json();
            document.getElementById('llmDot').className = 'status-dot ' + (data.available ? 'green' : 'red');
            document.getElementById('llmStatus').textContent = data.available ? data.model : 'unavailable';
        } catch(e) {
            document.getElementById('llmDot').className = 'status-dot red';
            document.getElementById('llmStatus').textContent = 'error';
        }
    })();

    // Check Intelligence LLM status
    (async function checkIntel() {
        try {
            const resp = await fetch('/audio-preprocess/intelligence-status');
            const data = await resp.json();
            document.getElementById('intelDot').className = 'status-dot ' + (data.available ? 'green' : 'red');
            document.getElementById('intelStatus').textContent = data.available ? data.model : 'unavailable';
        } catch(e) {
            document.getElementById('intelDot').className = 'status-dot red';
            document.getElementById('intelStatus').textContent = 'error';
        }
    })();

    // ═══════════════════════════════════════════
    // Run post-processing
    // ═══════════════════════════════════════════

    async function runPostProcessing() {
        const segments = parseTranscript(transcriptInput.value);
        if (segments.length === 0) {
            alert('No transcript text to process. Paste text or click Load Sample.');
            return;
        }

        const vocabEnabled = document.getElementById('vocabCheck').checked;
        const llmEnabled = document.getElementById('llmCheck').checked;
        const intelEnabled = document.getElementById('intelCheck').checked;
        const selectedVocabs = [...document.querySelectorAll('.vocab-checkbox:checked')].map(cb => cb.value);
        const language = document.getElementById('langSelect').value;

        if (!vocabEnabled && !intelEnabled) {
            alert('Enable at least one processing option (Vocabulary Correction or Call Intelligence).');
            return;
        }

        if (vocabEnabled && selectedVocabs.length === 0) {
            alert('Please select at least one vocabulary file.');
            return;
        }

        // Build payload
        const ppType = intelEnabled ? document.getElementById('ppTypeSelect').value : 'phase7';
        const payload = {
            segments: segments,
            vocab_correction: vocabEnabled,
            vocabulary: selectedVocabs.join(','),
            llm_correction: vocabEnabled && llmEnabled,
            enable_intelligence: intelEnabled,
            post_processing_type: ppType,
            language: language
        };

        // Show progress
        const btn = document.getElementById('runBtn');
        btn.disabled = true;
        const progress = document.getElementById('progress');
        progress.style.display = 'block';
        document.getElementById('results').style.display = 'none';

        // Animate progress
        let pct = 0;
        const steps = [];
        if (vocabEnabled) steps.push('Applying vocabulary corrections...');
        if (vocabEnabled && llmEnabled) steps.push('Running LLM correction...');
        if (intelEnabled) steps.push('Computing coaching metrics...', 'Running intelligence extraction...', 'Computing scores...');
        let stepIdx = 0;

        const progressInterval = setInterval(() => {
            if (pct < 90) {
                pct += intelEnabled ? 0.3 : 2;
                document.getElementById('progressFill').style.width = pct + '%';
                document.getElementById('progressPct').textContent = Math.floor(pct) + '%';
            }
            // Advance step text
            if (steps.length > 0) {
                const stepThreshold = 90 / steps.length;
                const newIdx = Math.min(Math.floor(pct / stepThreshold), steps.length - 1);
                if (newIdx !== stepIdx) {
                    stepIdx = newIdx;
                }
                document.getElementById('progressStep').textContent = steps[stepIdx];
            }
        }, 500);

        try {
            const resp = await fetch('/audio-preprocess/post-process/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });

            clearInterval(progressInterval);

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Server error');
            }

            const data = await resp.json();

            // Complete progress
            document.getElementById('progressFill').style.width = '100%';
            document.getElementById('progressPct').textContent = '100%';
            document.getElementById('progressStep').textContent = 'Done in ' + (data.processing_time_total || 0) + 's';

            displayResults(data);

        } catch(e) {
            clearInterval(progressInterval);
            document.getElementById('progressStep').textContent = 'Error: ' + e.message;
            document.getElementById('progressFill').style.background = '#f87171';
            document.getElementById('progressFill').style.width = '100%';
        } finally {
            btn.disabled = false;
        }
    }

    // ═══════════════════════════════════════════
    // Display results
    // ═══════════════════════════════════════════

    function displayResults(data) {
        const resultsDiv = document.getElementById('results');
        resultsDiv.style.display = 'block';

        // ── Vocabulary corrections ──
        const vocabDiv = document.getElementById('vocabResults');
        const vc = data.vocabulary || {};
        if (vc.enabled && vc.corrections && vc.corrections.length > 0) {
            let html = '<div class="section-title">Vocabulary Corrections (' + vc.corrections.length + ') &mdash; ' + vc.processing_time_seconds + 's</div>';
            html += '<table><thead><tr><th>Seg</th><th>Original</th><th>Corrected</th><th>Type</th><th>Score</th></tr></thead><tbody>';
            vc.corrections.forEach(c => {
                html += '<tr><td>' + (c.segment_id != null ? c.segment_id : '-') + '</td>' +
                    '<td style="color:#f87171; text-decoration:line-through;">' + escapeHtml(c.original_text || c.original) + '</td>' +
                    '<td class="correction-applied">' + escapeHtml(c.corrected_text || c.corrected) + '</td>' +
                    '<td><span class="badge" style="background:#22252f; color:#e0e0e0;">' + escapeHtml(c.correction_type || c.type || '-') + '</span></td>' +
                    '<td>' + (c.match_score != null ? c.match_score.toFixed(2) : '-') + '</td></tr>';
            });
            html += '</tbody></table>';
            vocabDiv.innerHTML = html;
        } else if (vc.enabled) {
            vocabDiv.innerHTML = '<div class="section-title">Vocabulary Corrections</div><p style="color:#888; font-size:13px;">No corrections needed.</p>';
        } else {
            vocabDiv.innerHTML = '';
        }

        // ── LLM corrections ──
        const llmDiv = document.getElementById('llmResults');
        const lc = data.llm_correction || {};
        if (lc.enabled && lc.corrections && lc.corrections.length > 0) {
            const applied = lc.corrections.filter(c => c.correction_type === 'llm_context' || c.type === 'llm_context');
            let html = '<div class="section-title">LLM Corrections (' + applied.length + ' applied, ' + lc.corrections.length + ' total) &mdash; ' + lc.processing_time_seconds + 's</div>';
            html += '<table><thead><tr><th>Seg</th><th>Original</th><th>Corrected</th><th>Status</th><th>Reasoning</th></tr></thead><tbody>';
            lc.corrections.forEach(c => {
                const isApplied = (c.correction_type || c.type) === 'llm_context';
                html += '<tr><td>' + (c.segment_id != null ? c.segment_id : '-') + '</td>' +
                    '<td style="' + (isApplied ? 'color:#f87171; text-decoration:line-through;' : '') + '">' + escapeHtml(c.original_text || c.original) + '</td>' +
                    '<td class="' + (isApplied ? 'correction-applied' : '') + '">' + escapeHtml(c.corrected_text || c.corrected || '-') + '</td>' +
                    '<td><span class="badge" style="background:' + (isApplied ? '#1a3a1a' : '#22252f') + '; color:' + (isApplied ? '#4ade80' : '#888') + ';">' + escapeHtml(c.correction_type || c.type || '-') + '</span></td>' +
                    '<td style="font-size:12px; color:#999;">' + escapeHtml(c.llm_reasoning || '-') + '</td></tr>';
            });
            html += '</tbody></table>';
            llmDiv.innerHTML = html;
        } else if (lc.enabled && !lc.available) {
            llmDiv.innerHTML = '<div class="section-title">LLM Corrections</div><p style="color:#f87171; font-size:13px;">Ollama not available.</p>';
        } else if (lc.enabled) {
            llmDiv.innerHTML = '<div class="section-title">LLM Corrections</div><p style="color:#888; font-size:13px;">No corrections needed.</p>';
        } else {
            llmDiv.innerHTML = '';
        }

        // ── Intelligence / Memoir ──
        const intelDiv = document.getElementById('intelligenceSection');
        const intel = data.intelligence || {};
        if (intel.enabled && intel.available && (intel.extraction || intel.coaching_metrics)) {
            if (intel.post_processing_type === 'memoir') {
                intelDiv.innerHTML = renderMemoirSummary(intel);
            } else {
                intelDiv.innerHTML = renderIntelligence(intel);
            }
        } else if (intel.enabled && !intel.available) {
            intelDiv.innerHTML = '<div class="section-title">Call Intelligence</div><p style="color:#f87171; font-size:13px;">Ollama not available for intelligence extraction.</p>';
        } else {
            intelDiv.innerHTML = '';
        }

        // ── Corrected transcript ──
        const transcriptDiv = document.getElementById('correctedTranscriptSection');
        if (data.transcription) {
            transcriptDiv.innerHTML = '<div class="section-title">Corrected Transcript</div>' +
                '<div class="transcription-box">' + escapeHtml(data.transcription) + '</div>' +
                '<div style="margin-top:8px; font-size:11px; color:#555;">Total processing time: ' + (data.processing_time_total || 0) + 's</div>';
        } else {
            transcriptDiv.innerHTML = '';
        }
    }

    // ═══════════════════════════════════════════
    // Rendering functions (duplicated from ui.py — keep in sync)
    // ═══════════════════════════════════════════

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = (seconds % 60).toFixed(2);
        return m > 0 ? m + ':' + s.padStart(5, '0') : s + 's';
    }

    const SPEAKER_COLORS = ['#60a5fa', '#4ade80', '#fbbf24', '#f87171', '#a78bfa', '#fb923c', '#2dd4bf', '#f472b6', '#818cf8', '#34d399'];

    function getSpeakerColor(speaker) {
        if (!speaker || speaker === 'UNKNOWN') return '#888';
        const match = speaker.match(/\\d+$/);
        if (match) {
            const idx = parseInt(match[0]) % SPEAKER_COLORS.length;
            return SPEAKER_COLORS[idx];
        }
        return SPEAKER_COLORS[0];
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
            // Second row
            html += '<div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-bottom:16px;">';
            html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                '<div style="font-size:11px; color:#888; margin-bottom:6px;">Interruptions</div>' +
                '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.interruption_count || 0) + '</div></div>';
            html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                '<div style="font-size:11px; color:#888; margin-bottom:6px;">Avg Response Time</div>' +
                '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.average_response_time_seconds || 0) + 's</div></div>';
            html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                '<div style="font-size:11px; color:#888; margin-bottom:6px;">Silence</div>' +
                '<div style="font-size:24px; font-weight:700; color:#fff;">' + (cm.silence_percentage || 0) + '%</div></div>';
            html += '</div>';
        }

        // ── Scoring ──
        const dh = ext.deal_health || {};
        const cs = ext.coaching_scorecard || {};
        const ms = ext.methodology_scores || {};
        if (dh.score !== undefined || cs.grade || ms.meddic) {
            html += '<div class="section-title">Scoring</div>';
            html += '<div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:16px;">';
            const dhColor = dh.label === 'healthy' ? '#4ade80' : (dh.label === 'at_risk' ? '#fbbf24' : '#f87171');
            html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                '<div style="font-size:11px; color:#888; margin-bottom:6px;">Deal Health</div>' +
                '<div style="font-size:28px; font-weight:700; color:' + dhColor + ';">' + (dh.score != null ? dh.score : '-') + '</div>' +
                '<div style="font-size:10px; color:' + dhColor + '; margin-top:4px; text-transform:uppercase; letter-spacing:1px;">' + (dh.label || '-').replace('_', ' ') + '</div></div>';
            const gradeColor = cs.grade === 'A' ? '#4ade80' : (cs.grade === 'B' ? '#60a5fa' : (cs.grade === 'C' ? '#fbbf24' : '#f87171'));
            html += '<div style="background:#1a1d27; border-radius:8px; padding:14px; text-align:center;">' +
                '<div style="font-size:11px; color:#888; margin-bottom:6px;">Coaching Grade</div>' +
                '<div style="font-size:36px; font-weight:700; color:' + gradeColor + ';">' + (cs.grade || '-') + '</div>' +
                '<div style="font-size:10px; color:#666; margin-top:4px;">Score: ' + (cs.score || 0) + '/100</div></div>';
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
                        (s.trigger ? ' &mdash; <em>' + escapeHtml(s.trigger) + '</em>' : '') + '</div>';
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
                const cs2 = c.sentiment === 'positive' ? '#4ade80' : (c.sentiment === 'negative' ? '#f87171' : '#fbbf24');
                html += '<tr><td style="font-weight:600;">' + escapeHtml(c.name) + '</td>' +
                    '<td style="color:' + cs2 + ';">' + (c.sentiment || '-') + '</td>' +
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
            html += '<div><table><thead><tr><th colspan="2">Budget</th></tr></thead><tbody>';
            html += '<tr><td>Discussed</td><td>' + (budget.discussed ? '<span style="color:#4ade80;">Yes</span>' : 'No') + '</td></tr>';
            if (budget.amount) html += '<tr><td>Amount</td><td>' + (budget.currency || '') + ' ' + budget.amount + '</td></tr>';
            html += '<tr><td>Pricing Presented</td><td>' + (budget.pricing_presented ? 'Yes' : 'No') + '</td></tr>';
            html += '<tr><td>Discount Requested</td><td>' + (budget.discount_requested ? 'Yes' : 'No') + '</td></tr>';
            html += '<tr><td>ROI Discussed</td><td>' + (budget.roi_discussed ? 'Yes' : 'No') + '</td></tr>';
            if (budget.quote) html += '<tr><td>Quote</td><td style="font-style:italic; color:#999; font-size:12px;">' + escapeHtml(budget.quote) + '</td></tr>';
            html += '</tbody></table></div>';
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

        // ── Processing Time ──
        html += '<div style="margin-top:12px; font-size:11px; color:#555;">Intelligence extraction time: ' + (intel.processing_time_seconds || 0) + 's</div>';

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
</script>
</body>
</html>
"""
