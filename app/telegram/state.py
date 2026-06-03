"""
Shared worker state for the single-process MVP bot.

SCALING BOUNDARY (read this before scaling out):
  These singletons hold the warning-hysteresis state and the pending
  notebook-tag context in process memory. That is correct and sufficient for
  the MVP's single worker process. When Jarvis moves to multi-process / queued
  dispatch (see worker.py), swap both for Redis-backed implementations that
  satisfy the same interfaces (rule_engine.ViolationStore and
  notebook.PendingTagStore) — no message-building logic changes, only these
  two constructors.
"""
from __future__ import annotations

from app.core.rule_engine import InMemoryViolationStore
from app.telegram.notebook import PendingTagStore

# Hysteresis store shared by rule_engine evaluation AND warning gating, so a
# warning that is "active" stays silent until the position resolves.
violation_store = InMemoryViolationStore()

# Pending notebook-tag context: code → PendingTag, resolved on button tap.
pending_tags = PendingTagStore()
