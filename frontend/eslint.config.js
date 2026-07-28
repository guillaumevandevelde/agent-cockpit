import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// `apiClient`/`apiUpload`/`apiAssetUrl` all prepend `API_BASE_URL` ('/api/v1/')
// to their endpoint argument. A caller that passes an absolute path builds
// `/api/v1//api/v1/...`, which 404s — Starlette does not normalise the double
// slash. Endpoints must therefore be relative ('mcp-server/tokens'), not
// absolute ('/api/v1/mcp-server/tokens').
const API_PREFIX_MESSAGE =
  "apiClient/apiUpload/apiAssetUrl prepend API_BASE_URL ('/api/v1/'). Pass a relative endpoint ('mcp-server/tokens'), not an absolute one ('/api/v1/mcp-server/tokens') — the latter builds '/api/v1//api/v1/...' and 404s."
const API_HELPERS = '/^(apiClient|apiUpload|apiAssetUrl)$/'

const noDoubleApiPrefix = [
  {
    // apiClient('/api/v1/foo')
    selector: `CallExpression[callee.name=${API_HELPERS}][arguments.0.value=/^\\//]`,
    message: API_PREFIX_MESSAGE,
  },
  {
    // apiClient(`/api/v1/foo/${id}`)
    selector: `CallExpression[callee.name=${API_HELPERS}][arguments.0.quasis.0.value.raw=/^\\//]`,
    message: API_PREFIX_MESSAGE,
  },
  {
    // const BASE = '/api/v1/subscriptions' — the per-feature api.ts convention;
    // an absolute BASE makes every `${BASE}/...` caller build a double prefix.
    selector: "VariableDeclarator[id.name='BASE'][init.value=/^\\/api\\/v1/]",
    message: API_PREFIX_MESSAGE,
  },
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-refresh/only-export-components': 'warn',
      '@typescript-eslint/no-explicit-any': 'warn',
      'no-restricted-syntax': ['error', ...noDoubleApiPrefix],
    },
  },
])
