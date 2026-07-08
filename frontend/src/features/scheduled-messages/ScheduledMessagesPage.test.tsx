// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const messages = [
  {
    id: 1,
    target_project: '/tmp/one',
    message: 'first',
    trigger_type: 'once',
    fire_at: '2999-01-01T09:00:00+00:00',
    cron_expr: null,
    timezone: 'UTC',
    permission_mode: 'acceptEdits',
    enabled: true,
    status: 'scheduled',
    on_missing_session: 'spawn',
    when_busy: 'wait_until_idle',
    target_kind: 'project',
    target_session_id: null,
    project_folder: null,
    session_preview: null,
    sandcastle_config_id: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    last_fired_at: null,
  },
  {
    id: 2,
    target_project: '/tmp/two',
    message: 'second',
    trigger_type: 'once',
    fire_at: '2999-01-02T09:00:00+00:00',
    cron_expr: null,
    timezone: 'UTC',
    permission_mode: 'acceptEdits',
    enabled: true,
    status: 'scheduled',
    on_missing_session: 'spawn',
    when_busy: 'wait_until_idle',
    target_kind: 'project',
    target_session_id: null,
    project_folder: null,
    session_preview: null,
    sandcastle_config_id: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    last_fired_at: null,
  },
]

vi.mock('./api', () => ({
  listScheduledMessages: vi.fn(async () => ({ items: messages })),
  deleteScheduledMessage: vi.fn(async () => {}),
  updateScheduledMessage: vi.fn(async () => {}),
  deleteScheduledMessageHistory: vi.fn(async () => ({ deleted: 0 })),
  bulkDeleteScheduledMessages: vi.fn(async () => ({ deleted: 2 })),
  getHooksStatus: vi.fn(async () => ({ events: {}, installed: true })),
  installHooks: vi.fn(async () => ({ events: {}, installed: true })),
  listDeliveryAttempts: vi.fn(async () => []),
}))

const { bulkDeleteScheduledMessages, listScheduledMessages } = await import('./api')
const { ScheduledMessagesPage } = await import('./ScheduledMessagesPage')

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ScheduledMessagesPage bulk delete', () => {
  it('lets the user select multiple messages and delete them together', async () => {
    render(<ScheduledMessagesPage />)

    await waitFor(() => expect(listScheduledMessages).toHaveBeenCalled())

    const checkboxes = await screen.findAllByRole('checkbox', { name: /select message/i })
    expect(checkboxes).toHaveLength(2)

    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])

    await screen.findByText('2 selected')

    fireEvent.click(screen.getByRole('button', { name: /delete selected/i }))

    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(bulkDeleteScheduledMessages).toHaveBeenCalledWith([1, 2]))
    await waitFor(() => expect(screen.queryByText('2 selected')).toBeNull())
  })
})
