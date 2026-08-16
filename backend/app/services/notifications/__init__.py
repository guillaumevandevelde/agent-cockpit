"""Outbound notification channels.

Single home for channels that push to the owner. Today: Telegram. Add new
channels here when §4 of ``docs/cockpit/cockpit-richting-decision.md`` grows
beyond two cases — keep the rule (push only on breakage or blockage) and
the failure semantics (never raise from a notifier) consistent across
channels.
"""
