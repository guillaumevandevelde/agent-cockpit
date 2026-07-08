import { BookOpen, CheckCircle2, Inbox, Plug, ShieldAlert, Terminal } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { MODAL_SIZES } from '@/lib/constants'

interface AgentMailHelpDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function HelpSection({ icon: Icon, title, children }: {
  icon: typeof BookOpen
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-lg border p-4">
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="space-y-2 text-sm leading-6 text-muted-foreground">{children}</div>
    </section>
  )
}

export function AgentMailHelpDialog({ open, onOpenChange }: AgentMailHelpDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Agent Mail setup</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <HelpSection icon={Plug} title="Required agent configuration">
            <p>
              Agents need two things: an MCP token (MCP Server page) so they can call the mail
              tools, and lifecycle hooks (Install tab) so mailbox state and reminders get injected
              into their session context automatically.
            </p>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">Claude Code: MCP token + hooks</Badge>
              <Badge variant="outline">Codex CLI: MCP token + hooks</Badge>
            </div>
          </HelpSection>

          <HelpSection icon={CheckCircle2} title="First run checklist">
            <ol className="list-decimal space-y-1 pl-5">
              <li>Create an MCP token on the MCP Server page and wire it into Claude Code/Codex config.</li>
              <li>Use the Install tab to add the lifecycle hooks.</li>
              <li>Restart or resume the affected agent sessions.</li>
              <li>Have each agent call <code>agent_mail_whoami</code> once from its repo.</li>
              <li>Ask agents to check <code>agent_mail_check_inbox</code> before and after major work.</li>
            </ol>
          </HelpSection>

          <HelpSection icon={Terminal} title="Non-tmux delivery">
            <p>
              Claude Code and Codex sessions outside tmux can receive mail through MCP, but
              Claude Cockpit cannot wake their visible terminal session yet. Those messages stay
              unread until the agent checks its inbox or reaches a hook boundary.
            </p>
          </HelpSection>

          <HelpSection icon={Inbox} title="What agents can do">
            <p>
              Agents can request context from another repo's agent, create handoffs, reply in
              threads, acknowledge answers, and list the team. The useful tools are
              <code> agent_mail_request_context</code>, <code> agent_mail_create_handoff</code>,
              <code> agent_mail_reply</code>, and <code> agent_mail_ack_message</code>.
            </p>
          </HelpSection>

          <HelpSection icon={ShieldAlert} title="Current limits">
            <p>
              Visibility is machine-global — every local participant is visible to every other
              participant. Identity is one participant per repository (git worktrees of the same
              repo share it); multiple simultaneous agents in the exact same repo currently share
              one mailbox.
            </p>
          </HelpSection>
        </div>
      </DialogContent>
    </Dialog>
  )
}
