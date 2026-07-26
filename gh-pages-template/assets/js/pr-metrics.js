'use strict';

function sortableValue(cell) {
    const text = cell.textContent.trim();
    if (!text || text === '—') return null;

    const duration = text.match(/^(-?[\d,.]+)\s*([dh])(?:\s|$)/i);
    if (duration) {
        const hours = Number(duration[1].replaceAll(',', ''));
        return duration[2].toLowerCase() === 'd' ? hours * 24 : hours;
    }

    const numeric = text.match(/^-?[\d,.]+(?:%|\s|$)/);
    if (numeric) return Number(numeric[0].replaceAll(/[,%\s]/g, ''));
    return text.toLocaleLowerCase();
}

function compareSortValues(left, right, direction) {
    if (left === null) return right === null ? 0 : 1;
    if (right === null) return -1;
    if (typeof left === 'number' && typeof right === 'number') {
        return (left - right) * direction;
    }
    return String(left).localeCompare(String(right), undefined, { numeric: true }) * direction;
}

function sortTable(table, column, direction) {
    const body = table.tBodies[0];
    if (!body) return;
    const rows = Array.from(body.rows).map((row, index) => ({ row, index }));
    rows.sort((left, right) => (
        compareSortValues(
            sortableValue(left.row.cells[column]),
            sortableValue(right.row.cells[column]),
            direction,
        ) || left.index - right.index
    ));
    rows.forEach(({ row }) => body.append(row));
}

function resetSortIndicators(headers) {
    headers.forEach(header => {
        header.removeAttribute('aria-sort');
        header.querySelector('.pr-metrics-sort-indicator').textContent = '↕';
    });
}

function activateSort(table, headers, header, indicator, column) {
    const ascending = header.getAttribute('aria-sort') !== 'ascending';
    resetSortIndicators(headers);
    header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
    indicator.textContent = ascending ? '↑' : '↓';
    sortTable(table, column, ascending ? 1 : -1);
}

function handleSortKey(event, activate) {
    if (['Enter', ' '].includes(event.key)) {
        event.preventDefault();
        activate();
    }
}

function initializeHeader(table, headers, header, column) {
    header.tabIndex = 0;
    header.style.cursor = 'pointer';
    header.style.userSelect = 'none';
    header.setAttribute('role', 'button');
    header.setAttribute('title', 'Sort by this column');
    const indicator = document.createElement('span');
    indicator.className = 'ms-1 pr-metrics-sort-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    indicator.textContent = '↕';
    header.append(indicator);

    const activate = () => activateSort(table, headers, header, indicator, column);
    header.addEventListener('click', activate);
    header.addEventListener('keydown', event => handleSortKey(event, activate));
}

function initializeTable(table) {
    if (table.dataset.sortableInitialized) return;
    table.dataset.sortableInitialized = 'true';
    const headers = Array.from(table.querySelectorAll('thead th'));
    headers.forEach((header, column) => initializeHeader(table, headers, header, column));
}

function initializeSortableTables(root = document) {
    root.querySelectorAll('table.pr-metrics-sortable').forEach(table => {
        initializeTable(table);
    });
}

document.addEventListener('DOMContentLoaded', () => initializeSortableTables());

/* istanbul ignore next */
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        sortableValue,
        compareSortValues,
        sortTable,
        initializeSortableTables,
    };
}
