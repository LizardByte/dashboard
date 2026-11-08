const {
    describe,
    test,
    expect,
    beforeEach,
} = require('@jest/globals');

function buildDom() {
    document.body.innerHTML = `
        <div id="loading-msg"></div>
        <div id="dashboard-content" style="display:none"></div>
        <div id="summary-stats"></div>
        <div id="chart-stars"></div>
        <div id="chart-star-history"></div>
        <div id="chart-forks"></div>
        <div id="chart-issues"></div>
        <div id="chart-code-scanning"></div>
        <div id="chart-code-scanning-history"></div>
        <div id="chart-prs"></div>
        <div id="table-prs"></div>
        <div id="chart-license"></div>
        <div id="chart-coverage"></div>
        <div id="chart-coverage-history"></div>
        <div id="chart-languages-bytes"></div>
        <div id="chart-languages-repos"></div>
        <div id="chart-commit-activity-weekly"></div>
        <div id="chart-commit-activity-repos"></div>
        <div id="chart-docs"></div>
        <div id="azure-signing-nav" hidden></div>
        <section id="azure-signing" hidden>
          <p id="azure-signing-status"></p>
          <div id="azure-signing-summary"></div>
          <div id="chart-azure-signing"></div>
        </section>
    `;
}

function sampleRepos() {
    return [
        {
            name: 'repo-a',
            archived: false,
            topics: [],
            stars: 5,
            forks: 3,
            issues: 2,
            issues_bot: 1,
            issues_other: 1,
            prs: 1,
            code_scanning_open: 4,
            license: 'MIT',
            coverage: 0,
            languages: { Python: 50 },
            has_readthedocs: true,
            fork: false,
        },
        {
            name: 'repo-b',
            archived: false,
            topics: ['package-manager'],
            stars: 1,
            forks: 1,
            issues: 0,
            issues_bot: 0,
            issues_other: 0,
            prs: 0,
            code_scanning_open: 0,
            license: null,
            coverage: 88,
            languages: { JavaScript: 20 },
            has_readthedocs: false,
            fork: true,
        },
    ];
}

describe('dashboard.js', () => {
    let mod;

    beforeEach(() => {
        jest.resetModules();
        buildDom();
        globalThis.DASHBOARD_CONFIG = { base_path: '/dashboard' };
        globalThis.matchMedia = jest.fn(() => ({ matches: false }));
        globalThis.Plotly = { newPlot: jest.fn(), purge: jest.fn() };
        globalThis.DataTable = undefined;
        globalThis.getComputedStyle = jest.fn(() => ({ getPropertyValue: () => '#fff' }));
        globalThis.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) }));
        mod = require('../gh-pages-template/assets/js/dashboard.js');
    });

    test('fetchJSON success and error', async () => {
        globalThis.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: 1 }) });
        await expect(mod.fetchJSON('x.json')).resolves.toEqual({ ok: 1 });

        globalThis.fetch.mockResolvedValueOnce({ ok: false, status: 500 });
        await expect(mod.fetchJSON('bad.json')).rejects.toThrow('Failed to fetch');
    });

    test('isDark and plotlyTemplate branches', () => {
        document.documentElement.dataset.bsTheme = 'dark';
        expect(mod.isDark()).toBe(true);
        expect(mod.plotlyTemplate()).toBe('plotly_dark');

        document.documentElement.dataset.bsTheme = 'light';
        expect(mod.isDark()).toBe(false);

        document.documentElement.dataset.bsTheme = '';
        document.documentElement.classList.add('dark-mode');
        expect(mod.isDark()).toBe(true);
        document.documentElement.classList.remove('dark-mode');
        globalThis.matchMedia = jest.fn(() => ({ matches: true }));
        expect(mod.isDark()).toBe(true);
    });

    test('themeLayout applies font color optionally', () => {
        const withFont = mod.themeLayout({ margin: { t: 1 } }, true);
        expect(withFont.font.color).toBe('#fff');

        globalThis.getComputedStyle = jest.fn(() => ({ getPropertyValue: () => '' }));
        const withoutColor = mod.themeLayout({}, true);
        expect(withoutColor.font).toBeUndefined();

        const disabled = mod.themeLayout({}, false);
        expect(disabled.font).toBeUndefined();
    });

    test('activeRepos filters archived and package-manager repos', () => {
        const result = mod.activeRepos(sampleRepos().concat([{ name: 'x', archived: true, topics: [] }]));
        expect(result.map(r => r.name)).toEqual(['repo-a']);
    });

    test('renderSummary handles missing and populated container', () => {
        document.getElementById('summary-stats').remove();
        mod.renderSummary([], [], { updated_at: new Date().toISOString() });

        const el = document.createElement('div');
        el.id = 'summary-stats';
        document.body.appendChild(el);
        mod.renderSummary(sampleRepos(), [], { updated_at: '2026-03-20T00:00:00Z' });
        expect(el.innerHTML).toContain('Active Repositories');
        expect(el.classList.contains('justify-content-center')).toBe(true);
    });

    test('renderBarChart handles missing/empty and populated entries', () => {
        mod.renderBarChart('missing-id', [{ name: 'a', value: 1 }]);
        mod.renderBarChart('chart-stars', []);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(0);

        mod.renderBarChart('chart-stars', [{ name: 'a', value: 1 }, { name: 'b', value: 3 }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(1);
    });

    test('repository labels omit unique owners and disambiguate duplicate names on a separate line', () => {
        const labels = mod.repositoryLabels([
            'LizardByte-infrastructure/upptime', 'First/demo', 'Second/demo', 'First/demo', 'legacy',
        ]);
        expect([...labels.values()]).toEqual(['upptime', 'demo<br>First', 'demo<br>Second', 'legacy']);
        expect(mod.repositoryLabels(['First/demo']).get('First/demo')).toBe('demo');
    });

    test('category charts use readable ticks while keeping full identities and values for hover', () => {
        const repos = ['First/demo', 'Second/demo', 'LizardByte-infrastructure/upptime'].map((name, index) => ({
            name, value: index + 1, issues: index + 1, coverage: 90 + index,
        }));
        mod.renderBarChart('chart-stars', repos);
        mod.renderIssuesBarChart(repos);
        mod.renderPRsBarChart(repos, repos.map(repo => ({ repo: repo.name, draft: false })));
        mod.renderCoverageChart(repos);
        for (const id of ['chart-stars', 'chart-issues', 'chart-prs', 'chart-coverage']) {
            const [, traces, layout] = globalThis.Plotly.newPlot.mock.calls.find(([chart]) => chart === id);
            const ticks = new Map(layout.xaxis.tickvals.map((name, index) => [name, layout.xaxis.ticktext[index]]));
            expect(ticks.get('LizardByte-infrastructure/upptime')).toBe('upptime');
            expect(ticks.get('First/demo')).toBe('demo<br>First');
            expect(ticks.get('Second/demo')).toBe('demo<br>Second');
            expect(layout.xaxis.automargin).toBe(true);
            expect(traces[0].x).toContain('First/demo');
            expect(traces[0].x).toContain('Second/demo');
            if (id === 'chart-coverage') {
                expect(traces[0].text).toContain('First/demo: 90.0%');
            } else {
                expect(traces[0].customdata).toEqual(traces[0].x);
                expect(traces[0].hovertemplate).toContain('%{customdata}');
            }
        }
    });

    test('history legends use short names without merging same-name series or hiding hover owners', () => {
        const names = ['First/demo', 'Second/demo', 'LizardByte-infrastructure/upptime'];
        const points = names.map((repo, index) => ({
            repo, date: '2026-11-07', week: '2026-11-07', stars: index + 1, coverage: 90 + index,
            open: index + 1, total: index + 1,
        }));
        mod.renderStarHistory(points);
        mod.renderCoverageHistory(points);
        mod.renderCodeScanningHistory(points);
        mod.renderCommitActivityChart(points, names.map(name => ({ name })));
        for (const id of ['chart-star-history', 'chart-coverage-history', 'chart-code-scanning-history',
            'chart-commit-activity-repos']) {
            const [, traces] = globalThis.Plotly.newPlot.mock.calls.find(([chart]) => chart === id);
            expect(traces).toHaveLength(3);
            expect(traces.find(trace => trace.meta === 'First/demo').name).toBe('demo<br>First');
            expect(traces.find(trace => trace.meta === 'Second/demo').name).toBe('demo<br>Second');
            expect(traces.find(trace => trace.meta === 'LizardByte-infrastructure/upptime').name).toBe('upptime');
            expect(traces.every(trace => trace.hovertemplate.includes('%{meta}'))).toBe(true);
        }
    });

    test('treemap labels are short while hover data and node identities retain the owners', () => {
        const repos = ['First/demo', 'Second/demo', 'LizardByte-infrastructure/upptime'].map(name => ({
            name, languages: { Python: 10, JavaScript: 5 }, has_readthedocs: true, license: 'MIT',
        }));
        mod.renderLicenseChart(repos);
        mod.renderDocsChart(repos);
        mod.renderLanguageCharts(repos);
        for (const [, [trace]] of globalThis.Plotly.newPlot.mock.calls) {
            expect(trace.labels[trace.customdata.indexOf('LizardByte-infrastructure/upptime')]).toBe('upptime');
            expect(trace.labels[trace.customdata.indexOf('First/demo')]).toBe('demo<br>First');
            expect(trace.labels[trace.customdata.indexOf('Second/demo')]).toBe('demo<br>Second');
            expect(trace.ids.some(id => id.includes('First/demo'))).toBe(true);
            expect(trace.ids.some(id => id.includes('Second/demo'))).toBe(true);
            expect(trace.hovertemplate).toContain('%{customdata}');
        }
    });

    test('renderIssuesBarChart splits bot and other issues', () => {
        const el = document.getElementById('chart-issues');
        el.remove();
        mod.renderIssuesBarChart(sampleRepos());
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(0);

        document.body.appendChild(el);
        mod.renderIssuesBarChart([{ name: 'empty', issues: 0 }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(0);

        mod.renderIssuesBarChart([
            { name: 'repo-a', issues: 3, issues_bot: 1 },
            { name: 'repo-b', issues: 2, issues_bot: 2, issues_other: 0 },
            { name: 'repo-c', issues: 1 },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(1);

        const [divId, traces, layout] = globalThis.Plotly.newPlot.mock.calls[0];
        expect(divId).toBe('chart-issues');
        expect(traces.map(trace => trace.name)).toEqual(['Other', 'Bot']);
        expect(traces[0].text).toEqual(['2', '', '1']);
        expect(traces[1].text).toEqual(['1', '2', '']);
        expect(layout.barmode).toBe('stack');
    });

    test('renderStarHistory branches', () => {
        mod.renderStarHistory([]);
        expect(document.getElementById('chart-star-history').innerHTML).toContain('No star history');

        mod.renderStarHistory([
            { repo: 'b', date: '2026-01-01', stars: undefined },
            { repo: 'a', date: '2026-01-01', stars: 2 },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();

        document.getElementById('chart-star-history').remove();
        mod.renderStarHistory([{ repo: 'a', date: '2026-01-01', stars: 1 }]);
    });

    test('renderPRsBarChart empty and populated and missing element', () => {
        const el = document.getElementById('chart-prs');
        el.remove();
        mod.renderPRsBarChart([{ name: 'repo-a' }], [{ repo: 'repo-a', draft: true }]);

        document.body.appendChild(el);
        mod.renderPRsBarChart([{ name: 'repo-a' }], []);
        expect(document.getElementById('chart-prs').innerHTML).toContain('No open PRs');

        mod.renderPRsBarChart(
            [{ name: 'repo-a' }, { name: 'repo-b' }],
            [
                { repo: 'repo-a', draft: false },
                { repo: 'repo-b', draft: true },
                { repo: 'missing', draft: false },
            ],
        );
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();
    });

    test('stripTags removes html tags', () => {
        expect(mod.stripTags('<b>hello</b> <i>x</i>')).toBe('hello x');
    });

    test('renderPRTable DataTable and fallback branches', () => {
        const prs = [
            {
                repo: 'repo-a',
                number: 1,
                title: 'T',
                author: 'A',
                labels: ['bug'],
                assignees: ['me'],
                draft: false,
                created_at: '2026-03-01T00:00:00Z',
                updated_at: '2026-03-02T00:00:00Z',
                milestone: null,
            },
            {
                repo: 'repo-b',
                number: 2,
                title: 'T2',
                author: 'B',
                labels: [],
                assignees: [],
                draft: true,
                created_at: null,
                updated_at: null,
                milestone: 'M',
            },
        ];

        const draw = jest.fn();
        const destroy = jest.fn();
        const dtCtor = jest.fn(() => ({ draw, destroy }));
        dtCtor.ext = { search: [] };
        globalThis.DataTable = dtCtor;

        mod.renderPRTable(prs);
        expect(dtCtor).toHaveBeenCalled();

        const input = document.querySelector('#table-prs thead tr:nth-child(2) input');
        input.value = 'repo-a';
        input.dispatchEvent(new Event('input'));
        expect(draw).toHaveBeenCalled();

        const filterFn = dtCtor.ext.search[0];
        expect(filterFn({ nTable: { id: 'other' } }, ['x'])).toBe(true);
        expect(filterFn({ nTable: { id: 'pr-datatable' } }, ['repo-a'])).toBe(true);
        input.value = 'zzz';
        input.dispatchEvent(new Event('input'));
        expect(filterFn({ nTable: { id: 'pr-datatable' } }, ['repo-a'])).toBe(false);

        globalThis.DataTable = undefined;
        mod.renderPRTable(prs);
        expect(destroy).toHaveBeenCalledTimes(1);
        expect(dtCtor.ext.search).toEqual([]);

        const fallbackInput = document.querySelector('#table-prs thead tr:nth-child(2) input');
        fallbackInput.value = 'none';
        fallbackInput.dispatchEvent(new Event('input'));

        const pageBtn = Array.from(document.querySelectorAll('#table-prs .page-link')).find(elm => elm.textContent === 'Next');
        pageBtn.click();
        const select = document.querySelector('#table-prs select');
        select.value = '25';
        select.dispatchEvent(new Event('change'));
        expect(document.querySelector('#table-prs small').textContent).toContain('Showing');

        document.getElementById('table-prs').remove();
        mod.renderPRTable(prs);
    });

    test('renderLicenseChart renders treemap', () => {
        mod.renderLicenseChart(sampleRepos().concat([{ ...sampleRepos()[0], name: 'repo-c' }]));
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();
    });

    test('renderAzureSigning hides unconfigured metrics and tolerates missing containers', () => {
        mod.renderAzureSigning(null);
        expect(document.getElementById('azure-signing').hidden).toBe(true);
        mod.renderAzureSigning({ status: 'disabled', daily: [] });
        document.getElementById('azure-signing-nav').remove();
        mod.renderAzureSigning(null);
        document.getElementById('azure-signing').remove();
        mod.renderAzureSigning(null);
        expect(globalThis.Plotly.newPlot).not.toHaveBeenCalled();
    });

    test('renderAzureSigning distinguishes unknown data, zero usage, and partial history', () => {
        mod.renderAzureSigning({ status: 'ready', daily: [{ date: '2026-10-01', completed: null }] });
        expect(document.getElementById('azure-signing-status').textContent).toContain('has not reported');
        expect(document.getElementById('chart-azure-signing').hidden).toBe(true);
        mod.renderAzureSigning({ status: 'error', daily: [] });
        expect(document.getElementById('azure-signing-status').textContent).toContain('temporarily unavailable');
        const data = {
            status: 'ready', collected_at: '2026-10-01T12:00:00Z',
            daily: [{ date: '2026-10-01', completed: 0 }],
            month_to_date: 0, last_30_days: null,
            month_to_date_complete: true, last_30_days_complete: false,
        };
        mod.renderAzureSigning(data);
        expect(document.getElementById('azure-signing').hidden).toBe(false);
        expect(document.getElementById('azure-signing-nav').hidden).toBe(false);
        expect(document.getElementById('chart-azure-signing').hidden).toBe(false);
        expect(document.getElementById('azure-signing-summary').textContent).toContain('Unavailable');
        expect(globalThis.Plotly.newPlot.mock.calls[0][1][0].y).toEqual([0]);
        data.status = 'error';
        data.month_to_date_complete = false;
        data.last_30_days_complete = true;
        mod.renderAzureSigning(data);
        expect(document.getElementById('azure-signing-status').textContent).toContain('could not be refreshed');
        expect(document.querySelectorAll('#azure-signing-summary h3')).toHaveLength(2);
        expect(document.getElementById('azure-signing-summary').textContent).toContain('partial history');
    });

    test('renderCoverageChart handles empty and non-empty', () => {
        mod.renderCoverageChart([{ name: 'x', coverage: 0 }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(0);

        mod.renderCoverageChart([{ name: 'x', coverage: 80 }, { name: 'y', coverage: 70 }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(1);
    });

    test('renderCoverageHistory branches', () => {
        const el = document.getElementById('chart-coverage-history');
        mod.renderCoverageHistory([]);
        expect(el.innerHTML).toContain('No coverage history');

        mod.renderCoverageHistory([
            { repo: 'a', date: '2026-01-01', coverage: 90 },
            { repo: 'a', date: '2026-01-02', coverage: 91 },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();

        el.remove();
        mod.renderCoverageHistory([{ repo: 'a', date: '2026-01-01', coverage: 90 }]);
    });

    test('renderCodeScanningHistory branches', () => {
        const el = document.getElementById('chart-code-scanning-history');
        mod.renderCodeScanningHistory([]);
        expect(el.innerHTML).toContain('No code scanning history');

        mod.renderCodeScanningHistory([
            { repo: 'c', date: '2026-01-01', open: undefined },
            { repo: 'b', date: '2026-01-01', open: undefined },
            { repo: 'a', date: '2026-01-01', open: 4 },
            { repo: 'a', date: '2026-01-02', open: 2 },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();

        el.remove();
        mod.renderCodeScanningHistory([{ repo: 'a', date: '2026-01-01', open: 1 }]);
    });

    test('treemap and renderLanguageCharts', () => {
        const treeA = mod.treemap({ Python: { total: 10, repos: { 'repo-a': 10 } } }, ([, info]) => info.total);
        expect(treeA.labels).toContain('Python');

        const treeB = mod.treemap({ Python: ['repo-a'] }, ([, arr]) => arr.length);
        expect(treeB.labels).toContain('repo-a');

        mod.renderLanguageCharts([
            { name: 'repo-a', languages: { Python: 10 } },
            { name: 'repo-a', languages: { Python: 5 } },
            { name: 'repo-b', languages: {} },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(2);
    });

    test('loadDashboard success and failure paths', async () => {
        const repos = sampleRepos();
        repos[1].topics = [];
        delete repos[1].code_scanning_open;
        const prs = [{ repo: 'repo-a', number: 1, draft: false }];
        const metadata = { updated_at: '2026-03-20T00:00:00Z' };
        const coverage = [
            { repo: 'repo-a', date: '2026-03-19', coverage: 77 },
            { repo: 'repo-a', date: '2026-03-18', coverage: 60 },
            { repo: 'repo-b', date: '2026-03-19', coverage: 88 },
        ];
        const commits = [{ repo: 'repo-a', week: '2026-01-01', total: 1 }];
        const stars = [{ repo: 'repo-a', date: '2026-01-01', stars: 3 }];
        const codeScanningHistory = [{ repo: 'repo-a', date: '2026-03-19', open: 4 }];
        const azureSigning = {
            status: 'ready', collected_at: '2026-03-20T00:00:00Z',
            daily: [{ date: '2026-03-19', completed: 12 }],
            month_to_date: 12, last_30_days: 12,
            month_to_date_complete: false, last_30_days_complete: false,
        };

        globalThis.fetch.mockImplementation(async (url) => {
            if (url.endsWith('repos.json')) return { ok: true, json: async () => repos };
            if (url.endsWith('prs.json')) return { ok: true, json: async () => prs };
            if (url.endsWith('metadata.json')) return { ok: true, json: async () => metadata };
            if (url.endsWith('coverage_history.json')) return { ok: true, json: async () => coverage };
            if (url.endsWith('commit_activity.json')) return { ok: true, json: async () => commits };
            if (url.endsWith('star_history.json')) return { ok: true, json: async () => stars };
            if (url.endsWith('code_scanning_history.json')) return { ok: true, json: async () => codeScanningHistory };
            if (url.endsWith('azure_signing.json')) return { ok: true, json: async () => azureSigning };
            return { ok: false, status: 404 };
        });

        await mod.loadDashboard();
        expect(document.getElementById('loading-msg').style.display).toBe('none');
        expect(document.getElementById('dashboard-content').style.display).toBe('');
        expect(document.getElementById('azure-signing').hidden).toBe(false);
        expect(globalThis.Plotly.newPlot.mock.calls.some(([id]) => id === 'chart-azure-signing')).toBe(true);

        document.getElementById('loading-msg').remove();
        document.getElementById('dashboard-content').remove();
        await mod.loadDashboard();

        buildDom();
        globalThis.fetch.mockImplementation(async (url) => {
            if (url.endsWith('repos.json')) return { ok: true, json: async () => repos };
            if (url.endsWith('prs.json')) return { ok: true, json: async () => prs };
            if (url.endsWith('metadata.json')) return { ok: true, json: async () => metadata };
            if (url.endsWith('coverage_history.json')) return { ok: false, status: 500 };
            if (url.endsWith('commit_activity.json')) return { ok: true, json: async () => commits };
            if (url.endsWith('star_history.json')) return { ok: true, json: async () => stars };
            if (url.endsWith('code_scanning_history.json')) return { ok: true, json: async () => codeScanningHistory };
            return { ok: false, status: 404 };
        });
        await mod.loadDashboard();

        globalThis.fetch.mockResolvedValue({ ok: false, status: 500 });
        await mod.loadDashboard();
        expect(document.getElementById('loading-msg').innerHTML).toContain('Failed to load dashboard data');

        document.getElementById('loading-msg').remove();
        await mod.loadDashboard();
    });

    test('organization selection scopes same-name repos, PR links and history, clearing empty charts', async () => {
        document.body.insertAdjacentHTML('beforeend', `
            <select id="organization-filter"></select><p id="collection-status" hidden></p>`);
        const repos = ['Second', 'First'].map(owner => ({
            ...sampleRepos()[0], name: `${owner}/demo`, owner,
        }));
        repos.push({ ...sampleRepos()[0], name: 'Empty/archived', owner: 'Empty', archived: true });
        const data = {
            'repos.json': repos,
            'prs.json': repos.slice(0, 2).map(repo => ({ repo: repo.name, number: 1 })),
            'metadata.json': { updated_at: '2026-11-07', registry_fresh: true },
            'star_history.json': repos.map(repo => ({ repo: repo.name, date: '2026-11-01', stars: 10 })),
            'coverage_history.json': repos.map(repo => ({ repo: repo.name, date: '2026-11-01', coverage: 90 })),
            'code_scanning_history.json': repos.map(repo => ({ repo: repo.name, date: '2026-11-01', open: 1 })),
            'commit_activity.json': repos.map(repo => ({ repo: repo.name, week: '2026-11-01', total: 3 })),
        };
        globalThis.fetch.mockImplementation(async url => ({
            ok: url.split('/').at(-1) in data,
            json: async () => data[url.split('/').at(-1)],
        }));
        await mod.loadDashboard();
        const select = document.getElementById('organization-filter');
        expect(Array.from(select.options).map(option => option.textContent))
            .toEqual(['All organizations', 'Empty', 'First', 'Second']);
        expect(document.querySelectorAll('#table-prs tbody tr')).toHaveLength(2);
        expect(document.querySelector('#table-prs a').href).toBe('https://github.com/Second/demo/pull/1');
        globalThis.Plotly.newPlot.mockClear();
        select.value = 'First';
        select.dispatchEvent(new Event('change'));
        expect(document.querySelectorAll('#table-prs tbody tr')).toHaveLength(1);
        expect(document.querySelector('#table-prs a').href).toBe('https://github.com/First/demo/pull/1');
        for (const id of ['chart-star-history', 'chart-coverage-history', 'chart-code-scanning-history',
            'chart-commit-activity-repos']) {
            const [, traces] = globalThis.Plotly.newPlot.mock.calls.find(([chart]) => chart === id);
            expect(traces.map(trace => trace.name)).toEqual(['demo']);
            expect(traces.map(trace => trace.meta)).toEqual(['First/demo']);
        }
        expect(document.getElementById('collection-status').hidden).toBe(true);
        select.value = 'Empty';
        document.getElementById('chart-stars').textContent = 'old chart';
        select.dispatchEvent(new Event('change'));
        expect(document.querySelectorAll('#table-prs tbody tr')).toHaveLength(0);
        expect(document.getElementById('chart-stars').textContent).toBe('');
        expect(globalThis.Plotly.purge).toHaveBeenCalledWith(document.getElementById('chart-stars'));
        expect(globalThis.Plotly.purge).not.toHaveBeenCalledWith(document.getElementById('chart-azure-signing'));
    });

    test('organization controls tolerate legacy records and report partial collections', () => {
        document.body.insertAdjacentHTML('beforeend', `
            <select id="organization-filter"></select><p id="collection-status"></p>`);
        mod.configureOrganizations([...sampleRepos(), { owner: 'Zeta' }, { owner: 'alpha' }, { owner: 'alpha' }],
            jest.fn());
        expect(Array.from(document.querySelectorAll('#organization-filter option')).map(option => option.textContent))
            .toEqual(['All organizations', 'alpha', 'Zeta']);
        const status = document.getElementById('collection-status');
        mod.renderCollectionStatus({});
        expect(status.hidden).toBe(true);
        mod.renderCollectionStatus({ registry_fresh: false, organization_status: [
            { name: 'First', status: 'stale' }, { name: 'Second', status: 'ok', codecov_status: 'stale' },
            { name: 'Third', status: 'ok', codecov_status: 'ok' },
        ] });
        expect(status.hidden).toBe(false);
        expect(status.textContent).toContain('organization list could not be refreshed');
        expect(status.textContent).toContain('First, Second');
        expect(status.textContent).not.toContain('Third');
    });

    test('DOMContentLoaded listener runs loadDashboard', async () => {
        globalThis.fetch.mockResolvedValue({ ok: false, status: 500 });
        document.dispatchEvent(new Event('DOMContentLoaded'));
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(document.getElementById('loading-msg').innerHTML).toContain('Failed to load dashboard data');
    });

    test('config fallback and base path normalization', async () => {
        jest.resetModules();
        buildDom();
        delete globalThis.DASHBOARD_CONFIG;
        globalThis.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ ok: 1 }) }));
        const fallbackMod = require('../gh-pages-template/assets/js/dashboard.js');
        await fallbackMod.fetchJSON('repos.json');
        expect(globalThis.fetch.mock.calls[0][0]).toContain('/dashboard/assets/data/repos.json');

        jest.resetModules();
        globalThis.DASHBOARD_CONFIG = { base_path: '///abc//def/' };
        globalThis.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ ok: 1 }) }));
        const normalizedMod = require('../gh-pages-template/assets/js/dashboard.js');
        await normalizedMod.fetchJSON('repos.json');
        expect(globalThis.fetch.mock.calls[0][0]).toContain('/abc/def/assets/data/repos.json');
    });

    test('activeRepos handles missing topics', () => {
        const result = mod.activeRepos([{ name: 'x', archived: false }]);
        expect(result).toHaveLength(1);
    });

    test('renderStarHistory reuses repo buckets', () => {
        mod.renderStarHistory([
            { repo: 'a', date: '2026-01-01', stars: 1 },
            { repo: 'a', date: '2026-01-02', stars: 2 },
            { repo: 'b', date: '2026-01-03', stars: 0 },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalled();
    });

    test('renderLanguageCharts handles missing languages object', () => {
        mod.renderLanguageCharts([{ name: 'repo-z' }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(2);
    });

    test('renderCommitActivityChart and renderDocsChart', () => {
        mod.renderCommitActivityChart([
            { repo: 'repo-a', week: '2026-01-01', total: 2 },
            { repo: 'repo-a', week: '2026-01-02', total: 3 },
            { repo: 'ignored', week: '2026-01-02', total: 3 },
        ], [{ name: 'repo-a' }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(2);

        globalThis.Plotly.newPlot.mockClear();
        mod.renderCommitActivityChart([], [{ name: 'repo-a' }]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(0);

        mod.renderDocsChart([
            { name: 'repo-has', has_readthedocs: true },
            { name: 'repo-none', has_readthedocs: false },
        ]);
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(1);
    });
});
