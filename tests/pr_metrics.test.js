const {
    describe,
    test,
    expect,
    beforeEach,
} = require('@jest/globals');

function buildTable() {
    document.body.innerHTML = `
        <table class="pr-metrics-sortable">
            <thead><tr><th>Repository</th><th>Count</th></tr></thead>
            <tbody>
                <tr><td>repo10</td><td><a>10</a></td></tr>
                <tr><td>repo2</td><td>2</td></tr>
                <tr><td>unknown</td><td>—</td></tr>
            </tbody>
        </table>`;
}

describe('pr-metrics.js', () => {
    let mod;

    beforeEach(() => {
        jest.resetModules();
        buildTable();
        mod = require('../gh-pages-template/assets/js/pr-metrics.js');
    });

    test('sortableValue parses empty, numeric, duration, reaction, and text values', () => {
        const cell = textContent => ({ textContent });
        expect(mod.sortableValue(cell(''))).toBeNull();
        expect(mod.sortableValue(cell('—'))).toBeNull();
        expect(mod.sortableValue(cell('1.5 h'))).toBe(1.5);
        expect(mod.sortableValue(cell('2 d'))).toBe(48);
        expect(mod.sortableValue(cell('1,234%'))).toBe(1234);
        expect(mod.sortableValue(cell('5 — 👍 3 ❤️ 2'))).toBe(5);
        expect(mod.sortableValue(cell('Repo 10'))).toBe('repo 10');
    });

    test('compareSortValues keeps missing values last and compares numbers and text', () => {
        expect(mod.compareSortValues(null, null, 1)).toBe(0);
        expect(mod.compareSortValues(null, 1, 1)).toBe(1);
        expect(mod.compareSortValues(1, null, -1)).toBe(-1);
        expect(mod.compareSortValues(1, 2, 1)).toBeLessThan(0);
        expect(mod.compareSortValues(1, 2, -1)).toBeGreaterThan(0);
        expect(mod.compareSortValues(1, '2', 1)).toBeLessThan(0);
        expect(mod.compareSortValues('repo2', 'repo10', 1)).toBeLessThan(0);
    });

    test('sortTable orders rows and preserves stable ties', () => {
        const table = document.querySelector('table');
        mod.sortTable(table, 1, 1);
        expect(Array.from(table.tBodies[0].rows, row => row.cells[0].textContent)).toEqual([
            'repo2', 'repo10', 'unknown',
        ]);

        table.tBodies[0].rows[0].cells[1].textContent = '10';
        mod.sortTable(table, 1, -1);
        expect(Array.from(table.tBodies[0].rows, row => row.cells[0].textContent)).toEqual([
            'repo2', 'repo10', 'unknown',
        ]);
        expect(() => mod.sortTable({ tBodies: [] }, 0, 1)).not.toThrow();
    });

    test('initializeSortableTables handles click and keyboard sorting once', () => {
        mod.initializeSortableTables();
        mod.initializeSortableTables();
        const table = document.querySelector('table');
        const headers = table.querySelectorAll('th');

        expect(headers[0].getAttribute('role')).toBe('button');
        expect(headers[0].querySelector('.pr-metrics-sort-indicator').textContent).toBe('↕');

        headers[1].click();
        expect(headers[1].getAttribute('aria-sort')).toBe('ascending');
        expect(headers[1].textContent).toContain('↑');

        headers[1].click();
        expect(headers[1].getAttribute('aria-sort')).toBe('descending');
        expect(headers[1].textContent).toContain('↓');

        headers[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        headers[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        expect(headers[0].getAttribute('aria-sort')).toBe('ascending');
        headers[0].dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
        expect(headers[0].getAttribute('aria-sort')).toBe('descending');

        document.dispatchEvent(new Event('DOMContentLoaded'));
        expect(table.dataset.sortableInitialized).toBe('true');
    });
});
