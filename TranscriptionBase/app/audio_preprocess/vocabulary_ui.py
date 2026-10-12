"""
HTML UI for the vocabulary management page (Phase 5a + 6a).
Separate from the main ui.py to keep file sizes manageable.
"""


def get_vocabulary_page() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vocabulary Manager</title>
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

        /* Header */
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
        h1 { color: #fff; font-size: 24px; }
        .back-link { color: #4a9eff; text-decoration: none; font-size: 14px; }
        .back-link:hover { text-decoration: underline; }

        /* Cards */
        .card {
            background: #1a1d27;
            border: 1px solid #2a2d37;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card h2 { color: #fff; font-size: 16px; margin-bottom: 16px; }

        /* Controls row */
        .controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
        .controls select, .controls input[type="text"] {
            background: #0f1117;
            border: 1px solid #333;
            color: #e0e0e0;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
        }
        .controls select { min-width: 200px; }
        .controls input[type="text"] { min-width: 250px; }

        /* Buttons */
        .btn {
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .btn-primary { background: #4a9eff; color: #fff; }
        .btn-primary:hover { background: #3a8eef; }
        .btn-success { background: #2ea043; color: #fff; }
        .btn-success:hover { background: #238636; }
        .btn-danger { background: #da3633; color: #fff; }
        .btn-danger:hover { background: #b62324; }
        .btn-secondary { background: #30363d; color: #e0e0e0; }
        .btn-secondary:hover { background: #484f58; }
        .btn-sm { padding: 4px 10px; font-size: 12px; }

        /* Table */
        .table-wrap { overflow-x: auto; margin-top: 12px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th {
            text-align: left;
            padding: 10px 8px;
            border-bottom: 2px solid #30363d;
            color: #8b949e;
            font-weight: 600;
            white-space: nowrap;
            cursor: pointer;
            user-select: none;
        }
        th:hover { color: #e0e0e0; }
        td {
            padding: 8px;
            border-bottom: 1px solid #21262d;
            vertical-align: top;
        }
        tr:hover { background: #161b22; }

        /* Tags */
        .tag {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 500;
            margin: 1px 2px;
        }
        .tag-product { background: #1f3a5f; color: #58a6ff; }
        .tag-competitor { background: #3d1f1f; color: #f85149; }
        .tag-person { background: #2d1f3d; color: #bc8cff; }
        .tag-jargon { background: #1f3d2d; color: #56d364; }
        .tag-abbreviation { background: #3d3d1f; color: #e3b341; }
        .tag-company { background: #1f2d3d; color: #79c0ff; }
        .tag-other { background: #2d2d2d; color: #8b949e; }

        /* Priority */
        .priority-1 { color: #8b949e; }
        .priority-2 { color: #e3b341; }
        .priority-3 { color: #f85149; font-weight: 600; }

        /* Add form */
        .add-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; align-items: end; }
        .add-form .form-group { display: flex; flex-direction: column; }
        .add-form label { font-size: 11px; color: #8b949e; margin-bottom: 4px; }
        .add-form input, .add-form select {
            background: #0f1117;
            border: 1px solid #333;
            color: #e0e0e0;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 13px;
        }
        .add-form .form-actions { display: flex; align-items: end; gap: 8px; }

        /* Edit row */
        .edit-input {
            background: #0f1117;
            border: 1px solid #4a9eff;
            color: #e0e0e0;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 13px;
            width: 100%;
        }

        /* Status bar */
        .status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            color: #8b949e;
            font-size: 12px;
        }

        /* Test match */
        .test-section { display: flex; gap: 12px; align-items: end; }
        .test-result {
            padding: 10px;
            border-radius: 6px;
            font-size: 13px;
            margin-top: 10px;
        }
        .test-match { background: #1f3d2d; border: 1px solid #2ea043; color: #56d364; }
        .test-no-match { background: #2d2d2d; border: 1px solid #30363d; color: #8b949e; }

        /* File input */
        .file-input-wrap {
            position: relative;
            display: inline-block;
        }
        .file-input-wrap input[type="file"] {
            position: absolute;
            opacity: 0;
            width: 100%;
            height: 100%;
            cursor: pointer;
        }

        /* Toast notification */
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 14px;
            z-index: 1000;
            transition: opacity 0.3s;
        }
        .toast-success { background: #1f3d2d; border: 1px solid #2ea043; color: #56d364; }
        .toast-error { background: #3d1f1f; border: 1px solid #da3633; color: #f85149; }

        /* Checkbox */
        .enabled-check { width: 16px; height: 16px; cursor: pointer; accent-color: #4a9eff; }

        /* Confirm dialog overlay */
        .overlay {
            display: none;
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.6);
            z-index: 999;
            align-items: center; justify-content: center;
        }
        .overlay.show { display: flex; }
        .dialog {
            background: #1a1d27;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 24px;
            max-width: 400px;
            text-align: center;
        }
        .dialog p { margin-bottom: 16px; }
        .dialog .btn { margin: 0 6px; }
    </style>
</head>
<body>

<div class="header">
    <div>
        <h1>Vocabulary Manager</h1>
        <span style="color: #8b949e; font-size: 13px;">Manage custom vocabulary for hotword boosting and transcription correction</span>
    </div>
    <a href="/audio-preprocess/" class="back-link">&larr; Back to Processing</a>
</div>

<!-- Vocabulary selector + actions -->
<div class="card">
    <div class="controls">
        <select id="vocabSelect" onchange="loadVocabulary()">
            <option value="">-- Select Vocabulary --</option>
        </select>
        <button class="btn btn-primary" onclick="createNew()">New Vocabulary</button>
        <div class="file-input-wrap">
            <button class="btn btn-secondary">Import JSON</button>
            <input type="file" accept=".json" onchange="importVocab(this)">
        </div>
        <button class="btn btn-secondary" onclick="exportVocab()" id="exportBtn" disabled>Export JSON</button>
    </div>
</div>

<!-- Add term form -->
<div class="card" id="addFormCard" style="display:none;">
    <h2>Add New Term</h2>
    <div class="add-form">
        <div class="form-group">
            <label>Term *</label>
            <input type="text" id="addTerm" placeholder="e.g. Salesforce">
        </div>
        <div class="form-group">
            <label>Category</label>
            <select id="addCategory">
                <option value="jargon">Jargon</option>
                <option value="abbreviation">Abbreviation</option>
                <option value="product">Product</option>
                <option value="competitor">Competitor</option>
                <option value="person">Person</option>
                <option value="company">Company</option>
                <option value="other">Other</option>
            </select>
        </div>
        <div class="form-group">
            <label>Sounds Like (comma-separated)</label>
            <input type="text" id="addSoundsLike" placeholder="sales farce, sales force">
        </div>
        <div class="form-group">
            <label>Context Words (comma-separated)</label>
            <input type="text" id="addContext" placeholder="CRM, platform">
        </div>
        <div class="form-group">
            <label>Priority</label>
            <select id="addPriority">
                <option value="1">1 - Normal</option>
                <option value="2" selected>2 - High</option>
                <option value="3">3 - Critical</option>
            </select>
        </div>
        <div class="form-actions">
            <button class="btn btn-success" onclick="addEntry()">Add Term</button>
        </div>
    </div>
</div>

<!-- Search + filter -->
<div class="card" id="searchCard" style="display:none;">
    <div class="controls">
        <input type="text" id="searchInput" placeholder="Search terms..." oninput="filterTable()">
        <select id="filterCategory" onchange="filterTable()">
            <option value="">All Categories</option>
            <option value="product">Product</option>
            <option value="competitor">Competitor</option>
            <option value="person">Person</option>
            <option value="jargon">Jargon</option>
            <option value="abbreviation">Abbreviation</option>
            <option value="company">Company</option>
            <option value="other">Other</option>
        </select>
        <span id="filterCount" style="color: #8b949e; font-size: 13px;"></span>
    </div>

    <!-- Terms table -->
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th onclick="sortTable('term')">Term</th>
                    <th onclick="sortTable('category')">Category</th>
                    <th>Sounds Like</th>
                    <th>Context Words</th>
                    <th onclick="sortTable('boost_priority')">Priority</th>
                    <th>Enabled</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="termsBody"></tbody>
        </table>
    </div>

    <div class="status-bar">
        <span id="statusText"></span>
    </div>
</div>

<!-- Test match -->
<div class="card" id="testCard" style="display:none;">
    <h2>Test Vocabulary Match</h2>
    <div class="test-section">
        <div class="form-group" style="display:flex;flex-direction:column;">
            <label style="font-size:11px;color:#8b949e;margin-bottom:4px;">Word to test</label>
            <input type="text" id="testWord" placeholder='e.g. "sales farce"' style="background:#0f1117;border:1px solid #333;color:#e0e0e0;padding:8px 12px;border-radius:6px;font-size:14px;min-width:250px;">
        </div>
        <button class="btn btn-primary" onclick="testMatch()" style="align-self:end;">Test Match</button>
    </div>
    <div id="testResult"></div>
</div>

<!-- Confirm dialog -->
<div class="overlay" id="confirmOverlay">
    <div class="dialog">
        <p id="confirmMsg">Are you sure?</p>
        <button class="btn btn-danger" id="confirmYes">Delete</button>
        <button class="btn btn-secondary" onclick="hideConfirm()">Cancel</button>
    </div>
</div>

<script>
const API = '/audio-preprocess/vocabulary';
let currentVocab = '';
let entries = [];
let sortKey = 'term';
let sortAsc = true;
let editingTerm = null;

// ── Init ──
async function init() {
    const resp = await fetch(API + '/list');
    const data = await resp.json();
    const sel = document.getElementById('vocabSelect');
    sel.innerHTML = '<option value="">-- Select Vocabulary --</option>';
    for (const v of data.vocabularies) {
        const opt = document.createElement('option');
        opt.value = v.name;
        opt.textContent = v.display_name + ' (' + v.entry_count + ' terms)';
        sel.appendChild(opt);
    }
}

async function loadVocabulary() {
    const name = document.getElementById('vocabSelect').value;
    if (!name) {
        document.getElementById('addFormCard').style.display = 'none';
        document.getElementById('searchCard').style.display = 'none';
        document.getElementById('testCard').style.display = 'none';
        document.getElementById('exportBtn').disabled = true;
        return;
    }
    currentVocab = name;
    const resp = await fetch(API + '/' + name + '/data');
    const data = await resp.json();
    entries = data.entries || [];
    document.getElementById('addFormCard').style.display = 'block';
    document.getElementById('searchCard').style.display = 'block';
    document.getElementById('testCard').style.display = 'block';
    document.getElementById('exportBtn').disabled = false;
    renderTable();
}

function renderTable() {
    const tbody = document.getElementById('termsBody');
    const search = document.getElementById('searchInput').value.toLowerCase();
    const catFilter = document.getElementById('filterCategory').value;

    let filtered = entries.filter(e => {
        if (search && !e.term.toLowerCase().includes(search) &&
            !(e.sounds_like || []).some(s => s.toLowerCase().includes(search)))
            return false;
        if (catFilter && e.category !== catFilter) return false;
        return true;
    });

    // Sort
    filtered.sort((a, b) => {
        let va = a[sortKey] || '';
        let vb = b[sortKey] || '';
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ? 1 : -1;
        return 0;
    });

    tbody.innerHTML = '';
    for (const e of filtered) {
        const tr = document.createElement('tr');
        tr.dataset.term = e.term;

        const catClass = 'tag-' + (e.category || 'other');
        const priClass = 'priority-' + e.boost_priority;
        const soundsLike = (e.sounds_like || []).map(s => '<span class="tag tag-other">' + esc(s) + '</span>').join(' ');
        const contextWords = (e.context_words || []).map(s => '<span class="tag tag-other">' + esc(s) + '</span>').join(' ');

        tr.innerHTML = `
            <td><strong>${esc(e.term)}</strong></td>
            <td><span class="tag ${catClass}">${esc(e.category || 'other')}</span></td>
            <td>${soundsLike || '<span style="color:#484f58">—</span>'}</td>
            <td>${contextWords || '<span style="color:#484f58">—</span>'}</td>
            <td><span class="${priClass}">${e.boost_priority}</span></td>
            <td><input type="checkbox" class="enabled-check" ${e.enabled ? 'checked' : ''} onchange="toggleEnabled('${esc(e.term)}', this.checked)"></td>
            <td>
                <button class="btn btn-secondary btn-sm" onclick="editEntry('${esc(e.term)}')">Edit</button>
                <button class="btn btn-danger btn-sm" onclick="confirmDelete('${esc(e.term)}')">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);
    }

    const enabledCount = entries.filter(e => e.enabled).length;
    document.getElementById('statusText').textContent =
        entries.length + ' total terms | ' + enabledCount + ' enabled | ' + filtered.length + ' shown';
    document.getElementById('filterCount').textContent = filtered.length !== entries.length ? filtered.length + ' of ' + entries.length : '';
}

function filterTable() { renderTable(); }

function sortTable(key) {
    if (sortKey === key) {
        sortAsc = !sortAsc;
    } else {
        sortKey = key;
        sortAsc = true;
    }
    renderTable();
}

function esc(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// ── CRUD ──
async function addEntry() {
    const term = document.getElementById('addTerm').value.trim();
    if (!term) { toast('Term is required', 'error'); return; }

    const entry = {
        term: term,
        category: document.getElementById('addCategory').value,
        sounds_like: document.getElementById('addSoundsLike').value.split(',').map(s => s.trim()).filter(Boolean),
        context_words: document.getElementById('addContext').value.split(',').map(s => s.trim()).filter(Boolean),
        boost_priority: parseInt(document.getElementById('addPriority').value),
        enabled: true,
    };

    const resp = await fetch(API + '/' + currentVocab + '/entry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry),
    });

    if (resp.ok) {
        entries.push(entry);
        renderTable();
        document.getElementById('addTerm').value = '';
        document.getElementById('addSoundsLike').value = '';
        document.getElementById('addContext').value = '';
        toast('Added: ' + term, 'success');
        refreshSelector();
    } else {
        const err = await resp.json();
        toast(err.detail || 'Failed to add', 'error');
    }
}

function editEntry(term) {
    const entry = entries.find(e => e.term === term);
    if (!entry) return;

    const tr = document.querySelector('tr[data-term="' + CSS.escape(term) + '"]');
    if (!tr) return;

    tr.innerHTML = `
        <td><input class="edit-input" id="editTerm" value="${esc(entry.term)}"></td>
        <td><select class="edit-input" id="editCategory">
            ${['product','competitor','person','jargon','abbreviation','company','other'].map(c =>
                '<option value="'+c+'" '+(entry.category===c?'selected':'')+'>'+c+'</option>'
            ).join('')}
        </select></td>
        <td><input class="edit-input" id="editSoundsLike" value="${esc((entry.sounds_like||[]).join(', '))}"></td>
        <td><input class="edit-input" id="editContext" value="${esc((entry.context_words||[]).join(', '))}"></td>
        <td><select class="edit-input" id="editPriority">
            <option value="1" ${entry.boost_priority===1?'selected':''}>1</option>
            <option value="2" ${entry.boost_priority===2?'selected':''}>2</option>
            <option value="3" ${entry.boost_priority===3?'selected':''}>3</option>
        </select></td>
        <td><input type="checkbox" class="enabled-check" id="editEnabled" ${entry.enabled?'checked':''}></td>
        <td>
            <button class="btn btn-success btn-sm" onclick="saveEdit('${esc(term)}')">Save</button>
            <button class="btn btn-secondary btn-sm" onclick="renderTable()">Cancel</button>
        </td>
    `;
    editingTerm = term;
}

async function saveEdit(originalTerm) {
    const updated = {
        term: document.getElementById('editTerm').value.trim(),
        category: document.getElementById('editCategory').value,
        sounds_like: document.getElementById('editSoundsLike').value.split(',').map(s => s.trim()).filter(Boolean),
        context_words: document.getElementById('editContext').value.split(',').map(s => s.trim()).filter(Boolean),
        boost_priority: parseInt(document.getElementById('editPriority').value),
        enabled: document.getElementById('editEnabled').checked,
    };

    if (!updated.term) { toast('Term is required', 'error'); return; }

    const resp = await fetch(API + '/' + currentVocab + '/entry/' + encodeURIComponent(originalTerm), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updated),
    });

    if (resp.ok) {
        const idx = entries.findIndex(e => e.term === originalTerm);
        if (idx >= 0) entries[idx] = updated;
        renderTable();
        toast('Updated: ' + updated.term, 'success');
    } else {
        const err = await resp.json();
        toast(err.detail || 'Failed to update', 'error');
    }
}

async function toggleEnabled(term, enabled) {
    const entry = entries.find(e => e.term === term);
    if (!entry) return;
    entry.enabled = enabled;

    await fetch(API + '/' + currentVocab + '/entry/' + encodeURIComponent(term), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry),
    });
    renderTable();
}

function confirmDelete(term) {
    document.getElementById('confirmMsg').textContent = 'Delete "' + term + '"?';
    document.getElementById('confirmYes').onclick = () => deleteEntry(term);
    document.getElementById('confirmOverlay').classList.add('show');
}

function hideConfirm() {
    document.getElementById('confirmOverlay').classList.remove('show');
}

async function deleteEntry(term) {
    hideConfirm();
    const resp = await fetch(API + '/' + currentVocab + '/entry/' + encodeURIComponent(term), {
        method: 'DELETE',
    });

    if (resp.ok) {
        entries = entries.filter(e => e.term !== term);
        renderTable();
        toast('Deleted: ' + term, 'success');
        refreshSelector();
    } else {
        toast('Failed to delete', 'error');
    }
}

// ── Import / Export ──
async function importVocab(input) {
    const file = input.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    const resp = await fetch(API + '/upload', { method: 'POST', body: formData });
    if (resp.ok) {
        const data = await resp.json();
        toast('Imported: ' + data.display_name + ' (' + data.entry_count + ' terms)', 'success');
        await init();
        document.getElementById('vocabSelect').value = data.name;
        await loadVocabulary();
    } else {
        const err = await resp.json();
        toast(err.detail || 'Import failed', 'error');
    }
    input.value = '';
}

function exportVocab() {
    if (!currentVocab) return;
    window.location.href = API + '/' + currentVocab + '/export';
}

function createNew() {
    const name = prompt('Enter vocabulary name:');
    if (!name) return;
    const safeName = name.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
    currentVocab = safeName;
    entries = [];

    // Create empty vocabulary via API
    fetch(API + '/' + safeName + '/entry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term: '__init__', category: 'other', sounds_like: [], context_words: [], boost_priority: 1, enabled: false }),
    }).then(async resp => {
        // Delete the placeholder
        await fetch(API + '/' + safeName + '/entry/__init__', { method: 'DELETE' });
        await init();
        document.getElementById('vocabSelect').value = safeName;
        await loadVocabulary();
        toast('Created: ' + name, 'success');
    });
}

// ── Test Match ──
async function testMatch() {
    const word = document.getElementById('testWord').value.trim();
    if (!word || !currentVocab) return;

    const resp = await fetch(API + '/test-match', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vocab_name: currentVocab, word: word, context: [] }),
    });

    const data = await resp.json();
    const div = document.getElementById('testResult');

    if (data.matched) {
        div.innerHTML = '<div class="test-result test-match">' +
            '<strong>"' + esc(word) + '"</strong> matches <strong>"' + esc(data.corrected_to) + '"</strong><br>' +
            'Score: ' + data.score + ' | Type: ' + data.match_type +
            '</div>';
    } else {
        div.innerHTML = '<div class="test-result test-no-match">' +
            '<strong>"' + esc(word) + '"</strong> does not match any vocabulary entry.' +
            '</div>';
    }
}

// ── Utilities ──
function toast(msg, type) {
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3000);
}

async function refreshSelector() {
    const current = currentVocab;
    await init();
    if (current) document.getElementById('vocabSelect').value = current;
}

// Start
init();
</script>
</body>
</html>
"""
