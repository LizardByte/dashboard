'use strict';

// Config
const _cfg = globalThis.DASHBOARD_CONFIG || {};
const basePath = _cfg.base_path
    ? ('/' + _cfg.base_path).replaceAll(/\/+/g, '/').replace(/\/$/, '')
    : '/dashboard';
const baseUrl = globalThis.location.origin + basePath;

// Helpers
async function fetchJSON(filename) {
    const url = `${baseUrl}/assets/data/${filename}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
    return resp.json();
}

function isDark() {
    const bsTheme = document.documentElement.dataset.bsTheme;
    if (bsTheme === 'dark') return true;
    if (bsTheme === 'light') return false;
    return document.documentElement.classList.contains('dark-mode')
        || globalThis.matchMedia('(prefers-color-scheme: dark)').matches;
}

function plotlyTemplate() {
    return isDark() ? 'plotly_dark' : 'plotly_white';
}

const LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { t: 30, r: 20, b: 120, l: 60 },
    autosize: true,
};
const CONFIG = { responsive: true };

function themeLayout(overrides = {}, applyFontColor = true) {
    const base = { ...LAYOUT, template: plotlyTemplate() };
    if (applyFontColor) {
        const textColor = getComputedStyle(document.documentElement).getPropertyValue('--bs-body-color').trim();
        if (textColor) base.font = { color: textColor };
    }
    return { ...base, ...overrides };
}

function activeRepos(repos) {
    return repos.filter(r => !r.archived && !(r.topics || []).includes('package-manager'));
}

// Summary
function renderSummary(active, prs, metadata) {
    const el = document.getElementById('summary-stats');
    if (!el) return;
    el.classList.add('justify-content-center');
    const updated = new Date(metadata.updated_at).toUTCString();
    const stat = (val, label) =>
        `<div class="col-6 col-sm-4 col-lg-2 mb-3 text-center">
            <h3 class="fw-bold">${val}</h3>
            <small class="text-muted">${label}</small>
         </div>`;
    el.innerHTML =
        stat(active.length, 'Active Repositories') +
        stat(active.filter(r => r.fork).length, 'Forked') +
        stat(active.reduce((s, r) => s + r.issues, 0), 'Open Issues') +
        stat(active.reduce((s, r) => s + r.prs, 0), 'Open PRs') +
        `<div class="col-12 text-center mt-1">
            <small class="text-muted">Last updated: ${updated}</small>
         </div>`;
}

// Bar charts
function renderBarChart(divId, entries) {
    const el = document.getElementById(divId);
    if (!el || !entries.length) return;
    const sorted = [...entries].sort((a, b) => b.value - a.value);
    Plotly.newPlot(divId, [{
        x: sorted.map(d => d.name),
        y: sorted.map(d => Math.log1p(d.value)),
        text: sorted.map(d => String(d.value)),
        type: 'bar',
        textposition: 'inside',
        marker: { color: '#28a9e6' },
    }], themeLayout({
        yaxis: { showticklabels: false },
        xaxis: { tickangle: -45 },
    }), CONFIG);
}

// Star history line chart
function renderStarHistory(history) {
    const el = document.getElementById('chart-star-history');
    if (!el) return;
    if (!history?.length) {
        el.innerHTML = '<p class="text-muted">No star history available.</p>';
        return;
    }
    const byRepo = {};
    history.forEach(({ repo, date, stars }) => {
        if (!byRepo[repo]) byRepo[repo] = { x: [], y: [] };
        byRepo[repo].x.push(date);
        byRepo[repo].y.push(stars);
    });
    const traces = Object.entries(byRepo)
        .sort((a, b) => {
            const lastA = a[1].y.at(-1) ?? 0;
            const lastB = b[1].y.at(-1) ?? 0;
            return lastB - lastA;
        })
        .map(([repo, d]) => ({ name: repo, x: d.x, y: d.y, mode: 'lines+markers', type: 'scatter' }));
    Plotly.newPlot('chart-star-history', traces, themeLayout({
        yaxis: { title: 'Stars' },
        margin: { t: 30, r: 20, b: 60, l: 60 },
    }), CONFIG);
}

// PRs bar chart
function renderPRsBarChart(active, prs) {
    const el = document.getElementById('chart-prs');
    if (!el) return;
    const counts = {};
    active.forEach(r => { counts[r.name] = { Ready: 0, Draft: 0 }; });
    prs.forEach(pr => {
        if (counts[pr.repo]) counts[pr.repo][pr.draft ? 'Draft' : 'Ready']++;
    });
    const sorted = active
        .map(r => ({ name: r.name, ...counts[r.name], total: counts[r.name].Ready + counts[r.name].Draft }))
        .filter(r => r.total > 0)
        .sort((a, b) => b.total - a.total);
    if (!sorted.length) {
        el.innerHTML = '<p class="text-muted">No open PRs.</p>';
        return;
    }
    Plotly.newPlot('chart-prs', ['Ready', 'Draft'].map(status => ({
        name: status,
        x: sorted.map(r => r.name),
        y: sorted.map(r => r[status]),
        text: sorted.map(r => String(r[status])),
        type: 'bar',
        textposition: 'inside',
    })), themeLayout({
        barmode: 'stack',
        xaxis: { tickangle: -45 },
    }), CONFIG);
}

function stripTags(html) {
    let out = '', inTag = false;
    for (const ch of html) {
        if (ch === '<') { inTag = true; }
        else if (ch === '>') { inTag = false; }
        else if (!inTag) { out += ch; }
    }
    return out;
}

// PR table
function renderPRTable(prs) {
    const container = document.getElementById('table-prs');
    if (!container) return;
    const cols = ['Repo', '#', 'Title', 'Author', 'Labels', 'Assignees', 'Status', 'Created', 'Last Activity', 'Milestone'];
    let rows = '';
    prs.forEach(pr => {
        const link = `<a href="https://github.com/LizardByte/${pr.repo}/pull/${pr.number}" target="_blank" rel="noopener">#${pr.number}</a>`;
        rows += `<tr>
            <td>${pr.repo}</td>
            <td>${link}</td>
            <td>${pr.title}</td>
            <td>${pr.author}</td>
            <td>${(pr.labels || []).join(', ')}</td>
            <td>${(pr.assignees || []).join(', ')}</td>
            <td>${pr.draft ? 'Draft' : 'Ready'}</td>
            <td>${(pr.created_at || '').substring(0, 10)}</td>
            <td>${(pr.updated_at || '').substring(0, 10)}</td>
            <td>${pr.milestone || ''}</td>
        </tr>`;
    });
    const headerCells = cols.map(c => `<th>${c}</th>`).join('');
    const filterCells = cols.map(() => `<th class="p-1"><input type="search" class="form-control form-control-sm" placeholder="Filter\u2026"></th>`).join('');
    container.innerHTML = `
        <table id="pr-datatable" class="table table-striped table-bordered table-sm w-100">
            <thead>
                <tr>${headerCells}</tr>
                <tr class="table-secondary">${filterCells}</tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;

    const filterInputs = Array.from(container.querySelectorAll('thead tr:nth-child(2) input'));
    const tbody = container.querySelector('tbody');

    if (typeof DataTable !== 'undefined') {
        const filterValues = new Array(cols.length).fill('');
        DataTable.ext.search.push((settings, rowData) => {
            if (settings.nTable.id !== 'pr-datatable') return true;
            return filterValues.every((val, i) => {
                if (!val) return true;
                return stripTags(String(rowData[i] || '')).toLowerCase().includes(val.toLowerCase());
            });
        });
        const dt = new DataTable('#pr-datatable', {
            pageLength: 25,
            lengthMenu: [10, 25, 50, 100],
            order: [[7, 'asc']],
            orderCellsTop: true,
        });
        filterInputs.forEach((input, i) => {
            input.addEventListener('input', () => {
                filterValues[i] = input.value;
                dt.draw();
            });
        });
        return dt;
    }

    // Fallback: sort by Created date (col 7) ascending, then add paging + per-column filtering.
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    allRows.sort((a, b) => {
        const aVal = a.querySelectorAll('td')[7]?.textContent || '';
        const bVal = b.querySelectorAll('td')[7]?.textContent || '';
        return aVal.localeCompare(bVal);
    });
    allRows.forEach(row => tbody.appendChild(row));

    let pageSize = 10;
    let currentPage = 0;

    const lengthSel = document.createElement('select');
    lengthSel.className = 'form-select form-select-sm d-inline-block w-auto';
    [10, 25, 50, 100].forEach(n => {
        const opt = document.createElement('option');
        opt.value = String(n);
        opt.textContent = String(n);
        if (n === 25) opt.selected = true;
        lengthSel.appendChild(opt);
    });
    const lengthLabel = document.createElement('label');
    lengthLabel.className = 'text-muted small me-auto';
    lengthLabel.append('Show ', lengthSel, ' entries');

    const infoEl = document.createElement('small');
    infoEl.className = 'text-muted';

    const pagUl = document.createElement('ul');
    pagUl.className = 'pagination pagination-sm mb-0';

    const topBar = document.createElement('div');
    topBar.className = 'd-flex align-items-center mb-2';
    topBar.appendChild(lengthLabel);

    const bottomBar = document.createElement('div');
    bottomBar.className = 'd-flex justify-content-between align-items-center mt-2 flex-wrap gap-2';
    bottomBar.append(infoEl, pagUl);

    const table = container.querySelector('table');
    table.before(topBar);
    container.appendChild(bottomBar);

    function getFiltered() {
        const filters = filterInputs.map(inp => inp.value.toLowerCase());
        return allRows.filter(row => {
            const cells = Array.from(row.querySelectorAll('td'));
            return filters.every((f, i) => !f || (cells[i]?.textContent || '').toLowerCase().includes(f));
        });
    }

    function renderPage() {
        const filtered = getFiltered();
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        currentPage = Math.min(currentPage, totalPages - 1);
        const start = currentPage * pageSize;

        allRows.forEach(r => { r.style.display = 'none'; });
        filtered.slice(start, start + pageSize).forEach(r => { r.style.display = ''; });

        const from = filtered.length === 0 ? 0 : start + 1;
        const to = Math.min(start + pageSize, filtered.length);
        infoEl.textContent = `Showing ${from} to ${to} of ${filtered.length} entries`;

        pagUl.innerHTML = '';
        const addBtn = (label, page, disabled, active) => {
            const li = document.createElement('li');
            li.className = `page-item${disabled ? ' disabled' : ''}${active ? ' active' : ''}`;
            const btn = document.createElement('button');
            btn.className = 'page-link';
            btn.textContent = label;
            btn.disabled = disabled;
            if (!disabled) btn.addEventListener('click', () => { currentPage = page; renderPage(); });
            li.appendChild(btn);
            pagUl.appendChild(li);
        };
        addBtn('Previous', currentPage - 1, currentPage === 0, false);
        const startP = Math.max(0, Math.min(currentPage - 2, totalPages - 5));
        for (let p = startP; p < Math.min(totalPages, startP + 5); p++) {
            addBtn(String(p + 1), p, false, p === currentPage);
        }
        addBtn('Next', currentPage + 1, currentPage >= totalPages - 1, false);
    }

    lengthSel.addEventListener('change', () => {
        pageSize = Number.parseInt(lengthSel.value, 10);
        currentPage = 0;
        renderPage();
    });
    filterInputs.forEach(inp => inp.addEventListener('input', () => { currentPage = 0; renderPage(); }));
    renderPage();
}

// License treemap
function renderLicenseChart(repos) {
    const licRepos = {};
    repos.forEach(r => {
        const lic = r.license || 'No License';
        if (!licRepos[lic]) licRepos[lic] = [];
        licRepos[lic].push(r.name);
    });
    const ids = [], labels = [], parents = [], values = [];
    Object.entries(licRepos).forEach(([lic, rlist]) => {
        ids.push(`lic::${lic}`);
        labels.push(lic);
        parents.push('');
        values.push(rlist.length);
        rlist.forEach(name => {
            ids.push(`repo::${lic}::${name}`);
            labels.push(name);
            parents.push(`lic::${lic}`);
            values.push(1);
        });
    });
    Plotly.newPlot('chart-license', [{
        type: 'treemap', ids, labels, parents, values,
        branchvalues: 'total',
        tiling: { squarifyratio: 1.618, pad: 2 },
        pathbar: { visible: false },
    }], themeLayout({
        margin: { t: 5, r: 5, b: 5, l: 5 },
    }, false), CONFIG);
}

// Coverage scatter
function renderCoverageChart(repos) {
    const sorted = [...repos].filter(r => r.coverage > 0).sort((a, b) => b.coverage - a.coverage);
    if (!sorted.length) return;
    Plotly.newPlot('chart-coverage', [{
        x: sorted.map(r => r.name),
        y: sorted.map(r => r.coverage),
        mode: 'markers',
        type: 'scatter',
        marker: {
            size: sorted.map(r => Math.max(8, 110 - r.coverage)),
            color: sorted.map(r => r.coverage),
            colorscale: [[0, 'red'], [0.5, 'yellow'], [1, 'green']],
            colorbar: { title: 'Coverage %' },
            showscale: true,
        },
        text: sorted.map(r => `${r.name}: ${r.coverage.toFixed(1)}%`),
    }], themeLayout({
        yaxis: { title: 'Coverage %', range: [0, 105] },
        xaxis: { tickangle: -45 },
    }), CONFIG);
}

// Coverage history line
function renderCoverageHistory(history) {
    const el = document.getElementById('chart-coverage-history');
    if (!el) return;
    if (!history?.length) {
        el.innerHTML = '<p class="text-muted">No coverage history available.</p>';
        return;
    }
    const byRepo = {};
    history.forEach(({ repo, date, coverage }) => {
        if (!byRepo[repo]) byRepo[repo] = { x: [], y: [] };
        byRepo[repo].x.push(date);
        byRepo[repo].y.push(coverage);
    });
    const traces = Object.entries(byRepo).map(([repo, d]) => ({
        name: repo, x: d.x, y: d.y, mode: 'lines', type: 'scatter',
    }));
    Plotly.newPlot('chart-coverage-history', traces, themeLayout({
        yaxis: { title: 'Coverage %', range: [0, 100] },
    }), CONFIG);
}

// Language treemaps
function treemap(data, valueFor) {
    const ids = [], labels = [], parents = [], values = [];
    const sorted = Object.entries(data).sort((a, b) => valueFor(b) - valueFor(a));
    sorted.forEach(([lang, info]) => {
        ids.push(`L::${lang}`);
        labels.push(lang);
        parents.push('');
        values.push(valueFor([lang, info]));
        const rlist = Array.isArray(info) ? info : Object.keys(info.repos);
        rlist.forEach(name => {
            ids.push(`R::${lang}::${name}`);
            labels.push(name);
            parents.push(`L::${lang}`);
            values.push(Array.isArray(info) ? 1 : info.repos[name]);
        });
    });
    return { ids, labels, parents, values };
}

function renderLanguageCharts(repos) {
    const byBytes = {}, byCount = {};
    repos.forEach(r => {
        Object.entries(r.languages || {}).forEach(([lang, bytes]) => {
            if (!byBytes[lang]) byBytes[lang] = { total: 0, repos: {} };
            byBytes[lang].total += bytes;
            byBytes[lang].repos[r.name] = (byBytes[lang].repos[r.name] || 0) + bytes;
            if (!byCount[lang]) byCount[lang] = [];
            if (!byCount[lang].includes(r.name)) byCount[lang].push(r.name);
        });
    });

    const treemapTrace = (data) => ({
        type: 'treemap', ...data,
        branchvalues: 'total',
        tiling: { squarifyratio: 1.618, pad: 2 },
        pathbar: { visible: false },
    });
    const treemapLayout = { margin: { t: 5, r: 5, b: 5, l: 5 } };

    Plotly.newPlot('chart-languages-bytes', [treemapTrace(treemap(byBytes, ([, info]) => info.total))],
        themeLayout(treemapLayout, false), CONFIG);

    Plotly.newPlot('chart-languages-repos', [treemapTrace(treemap(byCount, ([, arr]) => arr.length))],
        themeLayout(treemapLayout, false), CONFIG);
}

// Commit activity charts
function renderCommitActivityChart(commitActivity, active) {
    const activeNames = new Set(active.map(r => r.name));

    const byWeek = {};
    commitActivity.forEach(({ repo, week, total }) => {
        if (!activeNames.has(repo)) return;
        byWeek[week] = (byWeek[week] || 0) + total;
    });
    const weeks = Object.keys(byWeek).sort((a, b) => a.localeCompare(b));
    if (weeks.length) {
        Plotly.newPlot('chart-commit-activity-weekly', [{
            x: weeks,
            y: weeks.map(w => byWeek[w]),
            mode: 'lines',
            type: 'scatter',
            fill: 'tozeroy',
            line: { color: '#28a9e6' },
        }], themeLayout({
            yaxis: { title: 'Commits' },
        }), CONFIG);
    }

    const byRepo = {};
    commitActivity.forEach(({ repo, week, total }) => {
        if (!activeNames.has(repo)) return;
        if (!byRepo[repo]) byRepo[repo] = { x: [], y: [] };
        byRepo[repo].x.push(week);
        byRepo[repo].y.push(total);
    });
    const repoTraces = Object.entries(byRepo)
        .filter(([, d]) => d.x.length > 0)
        .map(([repo, d]) => {
            const pairs = d.x.map((w, i) => [w, d.y[i]]).sort((a, b) => a[0].localeCompare(b[0]));
            return {
                name: repo,
                x: pairs.map(([w]) => w),
                y: pairs.map(([, v]) => v),
                mode: 'lines',
                type: 'scatter',
            };
        });
    if (repoTraces.length) {
        Plotly.newPlot('chart-commit-activity-repos', repoTraces, themeLayout({
            yaxis: { title: 'Commits' },
            margin: { t: 30, r: 20, b: 60, l: 60 },
        }), CONFIG);
    }
}

// Docs treemap
function renderDocsChart(repos) {
    const hasRTD = repos.filter(r => r.has_readthedocs);
    const noRTD = repos.filter(r => !r.has_readthedocs);
    const ids = ['true', 'false',
        ...hasRTD.map(r => `y::${r.name}`),
        ...noRTD.map(r => `n::${r.name}`)];
    const labels = ['Has ReadTheDocs', 'No ReadTheDocs',
        ...hasRTD.map(r => r.name),
        ...noRTD.map(r => r.name)];
    const parents = ['', '',
        ...hasRTD.map(() => 'true'),
        ...noRTD.map(() => 'false')];
    const values = [hasRTD.length, noRTD.length,
        ...hasRTD.map(() => 1),
        ...noRTD.map(() => 1)];
    Plotly.newPlot('chart-docs', [{
        type: 'treemap', ids, labels, parents, values,
        branchvalues: 'total',
        tiling: { squarifyratio: 1.618, pad: 2 },
        pathbar: { visible: false },
    }], themeLayout({
        margin: { t: 5, r: 5, b: 5, l: 5 },
    }, false), CONFIG);
}

// Main
document.addEventListener('DOMContentLoaded', async () => {
    const loadingEl = document.getElementById('loading-msg');
    const contentEl = document.getElementById('dashboard-content');
    try {
        const [repos, prs, metadata, coverageHistory, commitActivity, starHistory] = await Promise.all([
            fetchJSON('repos.json'),
            fetchJSON('prs.json'),
            fetchJSON('metadata.json'),
            fetchJSON('coverage_history.json').catch(() => []),
            fetchJSON('commit_activity.json').catch(() => []),
            fetchJSON('star_history.json').catch(() => []),
        ]);

        const active = activeRepos(repos);
        const activePRs = prs.filter(pr => active.some(r => r.name === pr.repo));

        // Patch repos whose current coverage is 0 using their latest history value
        if (coverageHistory.length) {
            const latestByRepo = {};
            coverageHistory.forEach(({ repo, date, coverage }) => {
                if (!latestByRepo[repo] || date > latestByRepo[repo].date) {
                    latestByRepo[repo] = { date, coverage };
                }
            });
            active.forEach(r => {
                if (!r.coverage && latestByRepo[r.name]) {
                    r.coverage = latestByRepo[r.name].coverage;
                }
            });
        }

        if (loadingEl) loadingEl.style.display = 'none';
        if (contentEl) contentEl.style.display = '';

        renderSummary(active, activePRs, metadata);
        renderBarChart('chart-stars', active.map(r => ({ name: r.name, value: r.stars })));
        renderStarHistory(starHistory);
        renderBarChart('chart-forks', active.map(r => ({ name: r.name, value: r.forks })));
        renderBarChart('chart-issues', active.map(r => ({ name: r.name, value: r.issues })));
        renderPRsBarChart(active, activePRs);
        renderPRTable(activePRs);
        renderLicenseChart(active);
        renderCoverageChart(active);
        renderCoverageHistory(coverageHistory);
        renderLanguageCharts(active);
        renderCommitActivityChart(commitActivity, active);
        renderDocsChart(active);

    } catch (err) {
        if (loadingEl) loadingEl.innerHTML =
            `<div class="alert alert-danger">Failed to load dashboard data: ${err.message}</div>`;
    }
});
