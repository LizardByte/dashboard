import js from '@eslint/js';
import globals from 'globals';

export default [
    js.configs.recommended,
    {
        ignores: [
            'node_modules/**',
            'coverage/**',
            'gh-pages/**',
        ],
    },
    {
        languageOptions: {
            globals: {
                ...globals.browser,
                ...globals.node,
                ...globals.jquery,
                ...globals.jest,
                Plotly: 'readonly',
                DataTable: 'readonly',
                module: 'readonly',
                require: 'readonly',
            },
        },
    },
];
