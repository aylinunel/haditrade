"""Plain-English narration of journaled decisions. Narration never changes a decision.

The deterministic template is always available. When TRADING_NARRATOR=claude
and the `anthropic` package plus credentials are available, Claude rewrites the
recorded four-role reasons as a short debate summary. Narrations are cached in
their own SQLite table, outside the hash-chained decision journal, together with
the model and request id that produced them.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping

ROLE_TITLES = {'developer': 'Quant developer', 'researcher': 'Quant researcher',
               'senior skeptic': 'Senior skeptic', 'trader': 'Quant trader'}
DEFAULT_MODEL = 'claude-opus-5-5'
SYSTEM_PROMPT = (
    'You explain decisions made by a four-role paper-trading research system: a quant developer '
    '(causal signals), a quant researcher (learned probability and empirical after-cost edge), a senior '
    'skeptic (vetoes unsupported evidence and conflicting signals) and a quant trader (costs, sizing, risk). '
    'Write 3 to 6 short sentences for a non-expert, in the order the roles spoke. Use only facts and numbers '
    'present in the record; if a value is missing, say it is missing rather than estimating it. State the final '
    'outcome plainly. This is paper research: do not give investment advice, predict prices, or suggest '
    'changing limits.')


def _verdict(decision: Mapping[str, Any]) -> bool:
    return decision.get('approved', decision.get('eligible')) is True


def template(record: Mapping[str, Any]) -> str:
    if record.get('kind') == 'settings_change':
        return 'A human changed the paper risk settings. The change applies to new decisions only.'
    proposal = record.get('proposal', {})
    lines = [f"The quant developer saw a {proposal.get('side', '?')} signal on {proposal.get('symbol', '?')} "
             f"from the {proposal.get('strategy_id', '?')} strategy."]
    for role in record.get('roles', []):
        decision = role.get('decision', {})
        reasons = '; '.join(str(r).rstrip('. ') for r in decision.get('reasons', [])[:3]) or 'no reason recorded'
        verb = 'approved' if _verdict(decision) else 'rejected'
        lines.append(f"{ROLE_TITLES.get(role.get('role'), role.get('role'))} {verb}: {reasons}.")
    outcome = record.get('outcome')
    if outcome == 'paper_only_approved':
        order = record.get('order') or {}
        lines.append(f"Outcome: a paper order of about EUR {order.get('notional_eur', 0):.2f} was queued for the next bar's open. No real order exists.")
    else:
        lines.append('Outcome: rejected. Nothing was traded.')
    return ' '.join(lines)


class Narrator:
    def __init__(self, directory: str | Path):
        self.path = Path(directory) / 'narrations.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS narrations (decision_id TEXT PRIMARY KEY, '
                               'text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL)')
        self.model = os.getenv('TRADING_NARRATOR_MODEL', DEFAULT_MODEL)
        self._client = None

    @property
    def llm_enabled(self) -> bool:
        if os.getenv('TRADING_NARRATOR', '').lower() != 'claude':
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def status(self) -> dict:
        return dict(llm_enabled=self.llm_enabled, model=self.model if self.llm_enabled else None,
                    note='Claude narration is optional and can never alter a decision.')

    def _cached(self, decision_id: str) -> dict | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute('SELECT text, source, created_at FROM narrations WHERE decision_id=?', (decision_id,)).fetchone()
        return dict(text=row[0], source=json.loads(row[1]), created_at=row[2]) if row else None

    def _store(self, decision_id: str, text: str, source: dict) -> dict:
        created = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.execute('INSERT OR REPLACE INTO narrations VALUES (?,?,?,?)', (decision_id, text, json.dumps(source), created))
        return dict(text=text, source=source, created_at=created)

    def narrate(self, record: Mapping[str, Any]) -> dict:
        decision_id = record['decision_id']
        cached = self._cached(decision_id)
        if cached:
            return cached
        fallback = template(record)
        if not self.llm_enabled:
            return dict(text=fallback, source=dict(kind='template'), created_at=None)
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic(timeout=60.0, max_retries=2)
        payload = {k: record.get(k) for k in ('proposal', 'roles', 'outcome', 'order', 'evaluated_at')}
        try:
            response = self._client.beta.messages.create(
                model=self.model, max_tokens=4000, system=SYSTEM_PROMPT,
                output_config={'effort': 'low'},
                betas=['server-side-fallback-2026-07-01'], fallbacks='default',
                messages=[{'role': 'user', 'content': 'Decision record (JSON):\n' + json.dumps(payload, default=str)[:60000]}])
        except anthropic.AuthenticationError:
            return dict(text=fallback, source=dict(kind='template', error='Claude credentials missing or invalid'), created_at=None)
        except anthropic.RateLimitError:
            return dict(text=fallback, source=dict(kind='template', error='Claude rate limited; try again later'), created_at=None)
        except anthropic.APIStatusError as exc:
            return dict(text=fallback, source=dict(kind='template', error=f'Claude API error {exc.status_code}'), created_at=None)
        except anthropic.APIConnectionError:
            return dict(text=fallback, source=dict(kind='template', error='Claude API unreachable'), created_at=None)
        if response.stop_reason == 'refusal':
            return dict(text=fallback, source=dict(kind='template', error='Claude declined to narrate this record'), created_at=None)
        text = ''.join(block.text for block in response.content if block.type == 'text').strip()
        if not text:
            return dict(text=fallback, source=dict(kind='template', error='Empty narration'), created_at=None)
        return self._store(decision_id, text, dict(kind='claude', model=response.model, request_id=response._request_id))
