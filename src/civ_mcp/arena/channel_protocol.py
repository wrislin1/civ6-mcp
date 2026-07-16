from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Literal

from civ_mcp.arena.channel_terms import (
    TERM_REGISTRY,
    TermValidationContext,
    validate_term,
)
from civ_mcp.arena.config import ChannelRules


CORE_TERM_NAMES = tuple(TERM_REGISTRY)

CHANNEL_ACTION_NAMES = (
    "send_message",
    "propose_deal",
    "respond_to_deal",
    "fund_deal",
    "respond_to_payment",
)

_DEAL_ID_PATTERN = re.compile(r"deal-([0-9]{6})")


@dataclass(frozen=True)
class SendMessage:
    to_player: int
    text: str


@dataclass(frozen=True)
class ProposeDeal:
    to_player: int
    text: str
    favor: dict
    payment_gold: int
    timing: Literal["up_front", "on_delivery"]
    within: int


@dataclass(frozen=True)
class RespondToDeal:
    deal_id: str
    accept: bool


@dataclass(frozen=True)
class FundDeal:
    deal_id: str


@dataclass(frozen=True)
class RespondToPayment:
    deal_id: str
    accept: bool


ChannelAction = SendMessage | ProposeDeal | RespondToDeal | FundDeal | RespondToPayment


@dataclass(frozen=True)
class StagedChannelAction:
    source_id: str
    actor: int
    action: ChannelAction


@dataclass(frozen=True)
class ParsedChannelLine:
    line_index: int
    source_id: str
    actor: int
    action: ChannelAction | None
    error: str = ""

    @property
    def staged_action(self) -> StagedChannelAction | None:
        if self.action is None:
            return None
        return StagedChannelAction(self.source_id, self.actor, self.action)

    @property
    def staged(self) -> StagedChannelAction | None:
        return self.staged_action


def _validate_fields(
    name: str, args: object, required: frozenset[str]
) -> dict:
    if not isinstance(args, dict):
        raise ValueError(f"{name} arguments must be an object")
    unknown = sorted(set(args) - required)
    if unknown:
        raise ValueError(f"unknown field(s) for {name}: {', '.join(unknown)}")
    missing = sorted(required - set(args))
    if missing:
        raise ValueError(f"missing field(s) for {name}: {', '.join(missing)}")
    return args


def _require_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _require_counterparty(
    value: object, *, actor: int, enabled_players: frozenset[int]
) -> int:
    to_player = _require_integer(value, "to_player")
    if to_player == actor:
        raise ValueError("channel action cannot target self")
    if to_player not in enabled_players:
        raise ValueError(f"player {to_player} is not channel-enabled")
    return to_player


def _require_text(value: object, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{label} must be 1..{maximum} characters")
    return value


def _require_deal_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("malformed deal_id")
    match = _DEAL_ID_PATTERN.fullmatch(value)
    if match is None or int(match.group(1)) < 1:
        raise ValueError("malformed deal_id")
    return value


def _require_accept(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("accept must be a boolean")
    return value


def _require_exact_object(
    value: object, *, label: str, required: frozenset[str]
) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - required)
    if unknown:
        raise ValueError(f"unknown field(s) in {label}: {', '.join(unknown)}")
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"missing field(s) in {label}: {', '.join(missing)}")
    return value


def _require_favor(
    value: object,
    *,
    rules: ChannelRules,
    narrative_allowed: bool,
    term_validator: Callable[[dict], dict] | None,
) -> dict:
    favor = _require_exact_object(
        value, label="favor", required=frozenset({"term_type", "params"})
    )
    term_type = favor["term_type"]
    params = favor["params"]
    if not isinstance(term_type, str):
        raise ValueError("favor term_type must be a string")
    if not isinstance(params, dict):
        raise ValueError("favor params must be an object")

    if term_type == "narrative":
        if not narrative_allowed:
            raise ValueError("narrative terms require an active game master")
        narrative_params = _require_exact_object(
            params, label="narrative params", required=frozenset({"text"})
        )
        text = _require_text(
            narrative_params["text"], "narrative text", rules.max_narrative_chars
        )
        return {"term_type": "narrative", "params": {"text": text}}

    if term_type not in CORE_TERM_NAMES:
        raise ValueError(f"unknown favor term {term_type!r}")

    canonical = {
        "term_type": term_type,
        "params": copy.deepcopy(params),
    }
    if term_validator is None:
        return canonical

    validated = term_validator(copy.deepcopy(canonical))
    validated = _require_exact_object(
        validated, label="validated favor", required=frozenset({"term_type", "params"})
    )
    if validated["term_type"] not in CORE_TERM_NAMES:
        raise ValueError(f"unknown favor term {validated['term_type']!r}")
    if not isinstance(validated["params"], dict):
        raise ValueError("validated favor params must be an object")
    return copy.deepcopy(validated)


def _require_action_size(
    name: str, args: dict, rules: ChannelRules
) -> None:
    try:
        canonical = json.dumps(
            {"action": name, **args},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("channel action must be JSON-serializable") from exc
    if len(canonical.encode()) > rules.max_queued_action_bytes:
        raise ValueError(
            f"channel action must be at most {rules.max_queued_action_bytes} bytes"
        )


def parse_channel_action(
    name: str,
    args: dict,
    *,
    actor: int,
    enabled_players: frozenset[int],
    rules: ChannelRules,
    narrative_allowed: bool = False,
    term_validator: Callable[[dict], dict] | None = None,
) -> ChannelAction:
    actor = _require_integer(actor, "actor")
    if actor not in enabled_players:
        raise ValueError(f"actor {actor} is not channel-enabled")
    if name not in CHANNEL_ACTION_NAMES:
        raise ValueError(f"unknown channel action {name!r}")

    if name == "send_message":
        args = _validate_fields(
            name, args, frozenset({"to_player", "text"})
        )
        to_player = _require_counterparty(
            args["to_player"], actor=actor, enabled_players=enabled_players
        )
        text = _require_text(
            args["text"], "message text", rules.max_message_chars
        )
        canonical_args = {"to_player": to_player, "text": text}
        _require_action_size(name, canonical_args, rules)
        return SendMessage(to_player, text)

    if name == "propose_deal":
        args = _validate_fields(
            name,
            args,
            frozenset(
                {
                    "to_player",
                    "text",
                    "favor",
                    "payment_gold",
                    "timing",
                    "within",
                }
            ),
        )
        to_player = _require_counterparty(
            args["to_player"], actor=actor, enabled_players=enabled_players
        )
        text = _require_text(args["text"], "deal text", rules.max_message_chars)
        payment_gold = _require_integer(args["payment_gold"], "payment_gold")
        if not 1 <= payment_gold <= rules.max_payment_gold:
            raise ValueError(
                f"payment_gold must be 1..{rules.max_payment_gold}"
            )
        timing = args["timing"]
        if timing not in ("up_front", "on_delivery"):
            raise ValueError("timing must be up_front or on_delivery")
        within = _require_integer(args["within"], "within")
        if not 1 <= within <= rules.max_completion_turns:
            raise ValueError(f"within must be 1..{rules.max_completion_turns}")
        favor = _require_favor(
            args["favor"],
            rules=rules,
            narrative_allowed=narrative_allowed,
            term_validator=term_validator,
        )
        canonical_args = {
            "to_player": to_player,
            "text": text,
            "favor": favor,
            "payment_gold": payment_gold,
            "timing": timing,
            "within": within,
        }
        _require_action_size(name, canonical_args, rules)
        return ProposeDeal(
            to_player, text, favor, payment_gold, timing, within
        )

    if name == "respond_to_deal":
        args = _validate_fields(name, args, frozenset({"deal_id", "accept"}))
        deal_id = _require_deal_id(args["deal_id"])
        accept = _require_accept(args["accept"])
        canonical_args = {"deal_id": deal_id, "accept": accept}
        _require_action_size(name, canonical_args, rules)
        return RespondToDeal(deal_id, accept)

    if name == "fund_deal":
        args = _validate_fields(name, args, frozenset({"deal_id"}))
        deal_id = _require_deal_id(args["deal_id"])
        canonical_args = {"deal_id": deal_id}
        _require_action_size(name, canonical_args, rules)
        return FundDeal(deal_id)

    args = _validate_fields(name, args, frozenset({"deal_id", "accept"}))
    deal_id = _require_deal_id(args["deal_id"])
    accept = _require_accept(args["accept"])
    canonical_args = {"deal_id": deal_id, "accept": accept}
    _require_action_size(name, canonical_args, rules)
    return RespondToPayment(deal_id, accept)


def parse_cli_channel_lines(
    summary: str,
    *,
    run_id: str,
    actor: int,
    turn: int,
    enabled_players: frozenset[int],
    rules: ChannelRules,
    narrative_allowed: bool = False,
) -> tuple[ParsedChannelLine, ...]:
    parsed_lines: list[ParsedChannelLine] = []
    for line_index, line in enumerate(summary.splitlines()):
        if not line.startswith("CHANNEL "):
            continue
        line_bytes = line.encode("utf-8", errors="surrogatepass")
        digest = hashlib.sha256(line_bytes).hexdigest()[:16]
        source_id = f"cli:{run_id}:{actor}:{turn}:{line_index}:{digest}"
        try:
            payload = json.loads(line[len("CHANNEL ") :])
        except Exception:
            parsed_lines.append(
                ParsedChannelLine(line_index, source_id, actor, None, "invalid CHANNEL JSON")
            )
            continue
        if not isinstance(payload, dict):
            parsed_lines.append(
                ParsedChannelLine(line_index, source_id, actor, None, "invalid CHANNEL JSON")
            )
            continue

        name = payload.pop("action", None)
        if not isinstance(name, str):
            parsed_lines.append(
                ParsedChannelLine(
                    line_index,
                    source_id,
                    actor,
                    None,
                    "CHANNEL action must be a string",
                )
            )
            continue
        try:
            action = parse_channel_action(
                name,
                payload,
                actor=actor,
                enabled_players=enabled_players,
                rules=rules,
                narrative_allowed=narrative_allowed,
            )
        except Exception as exc:
            error = "invalid CHANNEL action"
            if type(exc) is ValueError:
                error = str(exc) or error
            parsed_lines.append(
                ParsedChannelLine(line_index, source_id, actor, None, error)
            )
        else:
            parsed_lines.append(
                ParsedChannelLine(line_index, source_id, actor, action)
            )
    return tuple(parsed_lines)


def _tool_schema(
    name: str, description: str, properties: dict, required: tuple[str, ...]
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def channel_tool_schemas(*, narrative_allowed: bool = False) -> list[dict]:
    term_names = list(CORE_TERM_NAMES)
    if narrative_allowed:
        term_names.append("narrative")
    player = {"type": "integer", "description": "Channel-enabled player ID."}
    message = {"type": "string", "minLength": 1, "maxLength": 2_000}
    deal_id = {
        "type": "string",
        "pattern": r"^deal-(?!000000)[0-9]{6}$",
    }
    accept = {"type": "boolean"}
    favor = {
        "type": "object",
        "properties": {
            "term_type": {"type": "string", "enum": term_names},
            "params": {"type": "object"},
        },
        "required": ["term_type", "params"],
        "additionalProperties": False,
    }
    return [
        _tool_schema(
            "send_message",
            "Send a private unofficial message to another channel-enabled player.",
            {"to_player": player, "text": message},
            ("to_player", "text"),
        ),
        _tool_schema(
            "propose_deal",
            "Propose an unofficial favor-for-gold deal.",
            {
                "to_player": player,
                "text": message,
                "favor": favor,
                "payment_gold": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10_000,
                },
                "timing": {
                    "type": "string",
                    "enum": ["up_front", "on_delivery"],
                },
                "within": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            ("to_player", "text", "favor", "payment_gold", "timing", "within"),
        ),
        _tool_schema(
            "respond_to_deal",
            "Accept or decline a proposed unofficial deal.",
            {"deal_id": deal_id, "accept": accept},
            ("deal_id", "accept"),
        ),
        _tool_schema(
            "fund_deal",
            "Offer the exact official gold payment for a due unofficial deal.",
            {"deal_id": deal_id},
            ("deal_id",),
        ),
        _tool_schema(
            "respond_to_payment",
            "Accept or reject the exact linked official gold payment.",
            {"deal_id": deal_id, "accept": accept},
            ("deal_id", "accept"),
        ),
    ]


@dataclass
class ChannelTurnContext:
    run_id: str
    player_id: int
    turn: int
    enabled_players: frozenset[int]
    rules: ChannelRules
    narrative_allowed: bool = False
    term_validator: Callable[[dict], dict] | None = None
    staged_actions: list[StagedChannelAction] = field(default_factory=list)

    def dispatch(self, name: str, args: dict) -> str:
        term_validator = self.term_validator
        if term_validator is None and name == "propose_deal":
            def registry_validator(term: dict) -> dict:
                return validate_term(
                    term,
                    TermValidationContext(
                        obligated_player=_require_integer(
                            args["to_player"], "to_player"
                        ),
                        enabled_players=self.enabled_players,
                    ),
                )

            term_validator = registry_validator

        action = parse_channel_action(
            name,
            args,
            actor=self.player_id,
            enabled_players=self.enabled_players,
            rules=self.rules,
            narrative_allowed=self.narrative_allowed,
            term_validator=term_validator,
        )
        index = len(self.staged_actions)
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        source_id = (
            f"api:{self.run_id}:{self.player_id}:{self.turn}:{index}:{digest}"
        )
        self.staged_actions.append(
            StagedChannelAction(source_id, self.player_id, action)
        )
        return (
            f"QUEUED channel action {source_id}; "
            "canonical result appears next turn"
        )


__all__ = [
    "CHANNEL_ACTION_NAMES",
    "CORE_TERM_NAMES",
    "ChannelAction",
    "ChannelTurnContext",
    "FundDeal",
    "ParsedChannelLine",
    "ProposeDeal",
    "RespondToDeal",
    "RespondToPayment",
    "SendMessage",
    "StagedChannelAction",
    "channel_tool_schemas",
    "parse_channel_action",
    "parse_cli_channel_lines",
]
