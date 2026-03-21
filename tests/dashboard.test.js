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
            prs: 1,
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
            prs: 0,
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
        globalThis.Plotly = { newPlot: jest.fn() };
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
        const dtCtor = jest.fn(() => ({ draw }));
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

        mod.renderDocsChart(sampleRepos());
        expect(globalThis.Plotly.newPlot).toHaveBeenCalledTimes(1);
    });

    test('loadDashboard success and failure paths', async () => {
        const repos = sampleRepos();
        const prs = [{ repo: 'repo-a', number: 1, draft: false }];
        const metadata = { updated_at: '2026-03-20T00:00:00Z' };
        const coverage = [
            { repo: 'repo-a', date: '2026-03-19', coverage: 77 },
            { repo: 'repo-a', date: '2026-03-18', coverage: 60 },
            { repo: 'repo-b', date: '2026-03-19', coverage: 88 },
        ];
        const commits = [{ repo: 'repo-a', week: '2026-01-01', total: 1 }];
        const stars = [{ repo: 'repo-a', date: '2026-01-01', stars: 3 }];

        globalThis.fetch.mockImplementation(async (url) => {
            if (url.endsWith('repos.json')) return { ok: true, json: async () => repos };
            if (url.endsWith('prs.json')) return { ok: true, json: async () => prs };
            if (url.endsWith('metadata.json')) return { ok: true, json: async () => metadata };
            if (url.endsWith('coverage_history.json')) return { ok: true, json: async () => coverage };
            if (url.endsWith('commit_activity.json')) return { ok: true, json: async () => commits };
            if (url.endsWith('star_history.json')) return { ok: true, json: async () => stars };
            return { ok: false, status: 404 };
        });

        await mod.loadDashboard();
        expect(document.getElementById('loading-msg').style.display).toBe('none');
        expect(document.getElementById('dashboard-content').style.display).toBe('');

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
            return { ok: false, status: 404 };
        });
        await mod.loadDashboard();

        globalThis.fetch.mockResolvedValue({ ok: false, status: 500 });
        await mod.loadDashboard();
        expect(document.getElementById('loading-msg').innerHTML).toContain('Failed to load dashboard data');

        document.getElementById('loading-msg').remove();
        await mod.loadDashboard();
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
});
