import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Claude Cockpit',
  description: 'Documentation for Claude Cockpit — Web dashboard for local AI coding agents',
  appearance: 'force-dark',
  base: '/docs/',
  head: [
    ['link', { rel: 'icon', href: '/docs/favicon.ico' }],
  ],

  srcExclude: [
    'plans-legacy/**',
    'superpowers/**',
    'cockpit/**',
  ],

  themeConfig: {
    logo: '/logo-dark.png',
    siteTitle: 'Claude Cockpit',

    nav: [
      { text: 'Guide', link: '/guide/' },
      { text: 'Features', link: '/features/dashboard' },
      { text: 'API Reference', link: '/api/' },
      {
        text: 'v1.3.0',
        link: 'https://github.com/guillaumevandevelde/claude-cockpit/blob/master/CHANGELOG.md',
      },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Guide',
          items: [
            { text: 'Introduction', link: '/guide/' },
            { text: 'Installation', link: '/guide/installation' },
            { text: 'Quick Start', link: '/guide/quick-start' },
            { text: 'Architecture', link: '/guide/architecture' },
            { text: 'Multi-Provider & Codex CLI', link: '/guide/multi-provider-codex-v2' },
            { text: 'Contributing', link: '/guide/contributing' },
          ],
        },
      ],
      '/features/': [
        {
          text: 'Features',
          items: [
            { text: 'Dashboard', link: '/features/dashboard' },
            { text: 'Config', link: '/features/config' },
            { text: 'MCP Servers', link: '/features/mcp-servers' },
            { text: 'MCP Server', link: '/features/mcp-server' },
            { text: 'Commands', link: '/features/commands' },
            { text: 'Plugins', link: '/features/plugins' },
            { text: 'Hooks', link: '/features/hooks' },
            { text: 'Permissions', link: '/features/permissions' },
            { text: 'Agents & Skills', link: '/features/agents-skills' },
            { text: 'Memory', link: '/features/memory' },
            { text: 'Output Styles', link: '/features/output-styles' },
            { text: 'Status Line', link: '/features/statusline' },
            { text: 'Sessions', link: '/features/sessions' },
            { text: 'Context Window', link: '/features/context' },
            { text: 'Usage Tracking', link: '/features/usage' },
            { text: 'Agent Bridge', link: '/features/agent-bridge' },
            { text: 'CC Bridge', link: '/features/cc-bridge' },
            { text: 'Presence', link: '/features/presence' },
            { text: 'Agent Mail', link: '/features/agent-mail' },
            { text: 'Scheduled Messages', link: '/features/scheduled-messages' },
            { text: 'Kanban', link: '/features/kanban' },
            { text: 'Agent Performance', link: '/features/agent-performance' },
            { text: 'Plans', link: '/features/plans' },
            { text: 'APM', link: '/features/apm' },
            { text: 'Hosts', link: '/features/hosts' },
            { text: 'Subscriptions', link: '/features/subscriptions' },
            { text: 'Updates', link: '/features/updates' },
            { text: 'Backup & Restore', link: '/features/backup' },
          ],
        },
      ],
      '/api/': [
        {
          text: 'API Reference',
          items: [
            { text: 'Overview', link: '/api/' },
            { text: 'Config', link: '/api/config' },
            { text: 'Providers', link: '/api/providers' },
            { text: 'MCP Servers', link: '/api/mcp' },
            { text: 'Commands', link: '/api/commands' },
            { text: 'Plugins', link: '/api/plugins' },
            { text: 'Hooks', link: '/api/hooks' },
            { text: 'Permissions', link: '/api/permissions' },
            { text: 'Agents', link: '/api/agents' },
            { text: 'Sessions', link: '/api/sessions' },
            { text: 'Context', link: '/api/context' },
            { text: 'Plans', link: '/api/plans' },
            { text: 'Output Styles', link: '/api/output-styles' },
            { text: 'Status Line', link: '/api/statusline' },
            { text: 'Agent Bridge', link: '/api/agent-bridge' },
            { text: 'CC Bridge', link: '/api/cc-bridge' },
            { text: 'Usage', link: '/api/usage' },
            { text: 'Memory', link: '/api/memory' },
            { text: 'Backup', link: '/api/backup' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/guillaumevandevelde/claude-cockpit' },
    ],

    search: {
      provider: 'local',
    },

    editLink: {
      pattern: 'https://github.com/guillaumevandevelde/claude-cockpit/edit/master/docs/:path',
      text: 'Edit this page on GitHub',
    },

    footer: {
      message: 'Released under the MIT License.',
      copyright: 'Copyright © 2026 Claude Cockpit Contributors',
    },
  },
})
