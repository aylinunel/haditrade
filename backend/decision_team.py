"""Four logical research roles, hard approval gates, and a durable paper-decision journal.

No LLM calls, broker operations, live orders, or automatic code/policy rewriting.
Injected agents must be pure evaluators; their evidence cannot override hard gates.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Protocol
from uuid import uuid4


class Agent(Protocol):
    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class GatePolicy:
    max_data_age_seconds: int = 120
    max_clock_skew_seconds: int = 5


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


_SENSITIVE = ('secret', 'password', 'token', 'credential', 'api_key', 'apikey', 'authorization')


def _safe(value: Any, depth: int = 0) -> Any:
    """Bounded structured audit data; credential fields are never persisted."""
    if depth > 8:
        return '[depth limit]'
    if isinstance(value, Mapping):
        return {str(k)[:100]: _safe(v, depth + 1) for k, v in list(value.items())[:100]
                if not any(word in str(k).lower() for word in _SENSITIVE)}
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, bool) or _number(value):
        return value
    return str(type(value).__name__)


class DecisionJournal:
    """Append-only SQLite audit with hash links and atomic transaction commits.

    Use a local private path outside the tracked repository. SQLite transaction
    locking serializes concurrent appends. Hashes detect edits, not authenticity.
    """
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS decisions '
                               '(sequence INTEGER PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL, '
                               'payload TEXT NOT NULL, previous_hash TEXT NOT NULL, digest TEXT NOT NULL)')
        self.path.chmod(0o600)

    def append(self, record: Mapping[str, Any]) -> str:
        payload = json.dumps(_safe(record), sort_keys=True, separators=(',', ':'), allow_nan=False)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT digest FROM decisions ORDER BY sequence DESC LIMIT 1').fetchone()
            previous_hash = previous[0] if previous else '0' * 64
            digest = hashlib.sha256((previous_hash + payload).encode()).hexdigest()
            connection.execute('INSERT INTO decisions(decision_id,payload,previous_hash,digest) VALUES(?,?,?,?)',
                               (record['decision_id'], payload, previous_hash, digest))
        return digest

    def records(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute('SELECT payload,previous_hash,digest FROM decisions ORDER BY sequence').fetchall()
        previous = '0' * 64
        result = []
        for payload, previous_hash, digest in rows:
            if previous_hash != previous or hashlib.sha256((previous_hash + payload).encode()).hexdigest() != digest:
                raise ValueError('Decision journal hash chain is invalid')
            result.append(json.loads(payload))
            previous = digest
        return result

    _verified: tuple[int, str] = (0, '0' * 64)

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        """Newest records after verifying rows appended since the last check.

        The full chain is verified once per process; later calls verify only
        new rows. `records()` remains the complete re-verification.
        """
        sequence, previous = self._verified
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute('SELECT sequence,payload,previous_hash,digest FROM decisions '
                                      'WHERE sequence > ? ORDER BY sequence', (sequence,)).fetchall()
            for number, payload, previous_hash, digest in rows:
                if previous_hash != previous or hashlib.sha256((previous_hash + payload).encode()).hexdigest() != digest:
                    raise ValueError('Decision journal hash chain is invalid')
                sequence, previous = number, digest
            self._verified = (sequence, previous)
            newest = connection.execute('SELECT payload FROM decisions ORDER BY sequence DESC LIMIT ?',
                                        (max(0, int(limit)),)).fetchall()
        return [json.loads(payload) for (payload,) in reversed(newest)]

    def find(self, decision_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute('SELECT payload FROM decisions WHERE decision_id = ?', (decision_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def count(self) -> int:
        with sqlite3.connect(self.path) as connection:
            return connection.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]


class UnconfiguredAgent:
    def __init__(self, role: str):
        self.role = role

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return {'eligible': False, 'approved': False,
                'reasons': [f'{self.role} evaluator is not configured; no eligibility or expected profit is inferred.']}


def _run(agent: Agent, context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = agent.evaluate(deepcopy(context))
        if not isinstance(result, Mapping):
            raise TypeError('Agent must return a mapping')
        return dict(result)
    except Exception as exc:
        # Exception messages may include external secrets; record class only.
        return {'eligible': False, 'approved': False,
                'reasons': [f'Evaluator failed ({type(exc).__name__}); proposal rejected.']}


def _eligible(result: Mapping[str, Any]) -> bool:
    return result.get('eligible', result.get('approved')) is True


class SeniorSkepticAgent:
    """Independent fail-closed checks. Confidence is never an expected return."""
    def __init__(self, policy: GatePolicy | None = None):
        self.policy = policy or GatePolicy()

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        proposal = context['proposal']
        market = context['market_state']
        now = context['now']
        researcher = context['researcher']
        reasons = []
        symbol, side = proposal.get('symbol'), proposal.get('side')
        if not isinstance(symbol, str) or not symbol.strip() or side not in ('long', 'short'):
            reasons.append('Symbol and direction must be valid.')
        if not isinstance(proposal.get('strategy_id'), str) or not proposal.get('strategy_id'):
            reasons.append('A strategy identifier is required.')
        confidence = proposal.get('confidence')
        if not _number(confidence) or not 0 <= confidence <= 1:
            reasons.append('Confidence must be finite and within 0–1.')
        if not _number(proposal.get('entry_reference_price')) or proposal['entry_reference_price'] <= 0:
            reasons.append('A finite positive reference price is required.')
        stop = proposal.get('stop_distance_pct')
        if not _number(stop) or not 0 < stop < 1:
            reasons.append('Stop distance must be a fraction strictly between 0 and 1.')
        if not proposal.get('evidence'):
            reasons.append('Proposal evidence is missing.')
        if context['portfolio'].get('team_batch_reserved') is True:
            reasons.append('An earlier proposal reserved this batch; transactional multi-order reservation is not implemented.')
        if market.get('data_valid') is not True:
            reasons.append('Validated market data is required.')
        for label, timestamp in [('Proposal', proposal.get('observed_at')), ('Market', market.get('observed_at'))]:
            observed = _time(timestamp)
            age = (now - observed).total_seconds() if observed else None
            if age is None or age > self.policy.max_data_age_seconds or age < -self.policy.max_clock_skew_seconds:
                reasons.append(f'{label} timestamp is missing, invalid, stale, or in the future.')
        if any(peer.get('symbol') == symbol and peer.get('side') in ('long', 'short') and peer.get('side') != side
               for peer in context.get('peer_proposals', [])):
            reasons.append('Opposite proposals for this symbol conflict; neither is selected automatically.')
        if side == 'short' and (market.get('short_supported') is not True or market.get('borrow_available') is not True):
            reasons.append('Shorting needs explicit instrument support and available borrow.')
        if not _eligible(context['developer']):
            reasons.append('Developer checks did not approve the proposal.')
        if not _eligible(researcher):
            reasons.append('Research evidence did not establish eligibility.')
        gross = researcher.get('expected_advantage_bps')
        costs = researcher.get('roundtrip_cost_bps')
        if (researcher.get('edge_supported') is not True or not _number(gross)
                or not _number(costs) or costs < 0 or gross - costs <= 0):
            reasons.append('A supported positive expected advantage after all round-trip costs is required.')
        return {'approved': not reasons, 'eligible': not reasons, 'reasons': reasons or [
            'Data, direction, shortability and independent after-cost evidence passed; trader risk approval is still required.'],
            'evidence': {'expected_gross_advantage_bps': gross if _number(gross) else None,
                         'roundtrip_cost_bps': costs if _number(costs) else None,
                         'expected_net_advantage_bps': gross - costs if _number(gross) and _number(costs) else None}}


class DecisionTeam:
    """Sequential four-role evaluation. Returns paper proposals, never places orders."""
    def __init__(self, journal: DecisionJournal, developer: Agent | None = None,
                 researcher: Agent | None = None, trader: Agent | None = None,
                 policy: GatePolicy | None = None):
        self.journal = journal
        self.developer = developer or UnconfiguredAgent('developer')
        self.researcher = researcher or UnconfiguredAgent('researcher')
        self.skeptic = SeniorSkepticAgent(policy)
        self.trader = trader or UnconfiguredAgent('trader')

    def evaluate(self, proposal: Mapping[str, Any], market_state: Mapping[str, Any],
                 portfolio: Mapping[str, Any], *, peer_proposals: list[Mapping[str, Any]] | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
        at = now or datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError('Evaluation time must include timezone')
        context = {'proposal': deepcopy(dict(proposal)), 'market_state': deepcopy(dict(market_state)),
                   'portfolio': deepcopy(dict(portfolio)), 'peer_proposals': deepcopy(peer_proposals or []),
                   'now': at.astimezone(timezone.utc), 'mode': 'paper'}
        context['developer'] = _run(self.developer, context)
        context['researcher'] = _run(self.researcher, context)
        context['senior_skeptic'] = _run(self.skeptic, context)
        # Trader evaluates risk even on vetoed candidates so every role explains
        # its view. Evaluators must be pure: no broker operation is permitted.
        trader = _run(self.trader, context)
        if context['senior_skeptic'].get('approved') is not True:
            trader = {**trader, 'approved': False, 'outcome': 'rejected', 'order': None,
                      'reasons': list(trader.get('reasons', [])) + [
                          'Independent approval gates failed; no paper order may proceed.']}
        approved = context['senior_skeptic'].get('approved') is True and trader.get('approved') is True
        record = {'decision_id': str(uuid4()), 'evaluated_at': at.isoformat(), 'mode': 'paper',
                  'live_orders_enabled': False, 'proposal': _safe(context['proposal']),
                  'roles': [{'role': 'developer', 'decision': _safe(context['developer'])},
                            {'role': 'researcher', 'decision': _safe(context['researcher'])},
                            {'role': 'senior skeptic', 'decision': _safe(context['senior_skeptic'])},
                            {'role': 'trader', 'decision': _safe(trader)}],
                  'outcome': 'paper_only_approved' if approved else 'rejected',
                  'order': _safe(trader.get('order')) if approved else None}
        try:
            record['journal_digest'] = self.journal.append(record)
            record['journal_persisted'] = True
        except (OSError, sqlite3.Error, ValueError, TypeError):
            record.update(outcome='rejected', order=None, journal_persisted=False,
                          audit_error='Durable audit write failed; proposal cannot proceed.')
        return record

    def evaluate_batch(self, proposals: list[Mapping[str, Any]],
                       markets: Mapping[str, Mapping[str, Any]], portfolio: Mapping[str, Any],
                       *, now: datetime | None = None,
                       peers: list[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        """All proposals share conflict evidence; aggregate sizing needs trader state.

        `peers` lists every currently active signal (default: `proposals`), so a
        fresh signal still conflicts with an opposite signal that is persisting.
        Without a transactional reservation ledger, allow at most one approval
        per batch. Sequential later candidates are recorded as held proposals,
        avoiding independent agents spending the same cash.
        """
        results = []
        reserved = False
        for proposal in proposals:
            effective_portfolio = deepcopy(dict(portfolio))
            if reserved:
                # Deny subsequent approvals without mutating caller account state.
                effective_portfolio['team_batch_reserved'] = True
            result = self.evaluate(proposal, markets.get(proposal.get('symbol'), {}),
                                   effective_portfolio, peer_proposals=list(peers if peers is not None else proposals), now=now)
            results.append(result)
            reserved = reserved or result['outcome'] == 'paper_only_approved'
        return results
