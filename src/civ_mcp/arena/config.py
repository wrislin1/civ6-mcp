from __future__ import annotations
from dataclasses import dataclass, field

# Canonical in-process LLM gateway endpoint; single source of truth for both the
# ArenaConfig default and the --gateway-url CLI default.
DEFAULT_GATEWAY_URL = "http://192.168.20.196:11444/v1"

VALID_SECTIONS = (
    "promotions",
    "overview",
    "units",
    "cities",
    "map",
    "research",
    "production_options",
    "empire_resources",
    "great_people",
    "rivals",
    "threats",
    "victory",
)

VALID_PLAYBOOKS = ("none", "condensed")

STANDING_PLAN_CAPTURE_CHARS = 4000
STANDING_PLAN_BASE_TASK_CAP = 8
STANDING_PLAN_CHARS_PER_EXTRA_TASK = 120

CLI_PROVIDER_COMMANDS = {"cli-claude": "claude", "cli-codex": "codex"}
_CLI_PROVIDERS = set(CLI_PROVIDER_COMMANDS)
# `scripted` selects the deterministic no-LLM ScriptedPolicy for that seat,
# needs no backend/CLI/tuner handoff, and has exactly two sanctioned uses:
# the mixed stage-1 seat-0 live gate (Task 9, test-only) and the live-gate
# passive privacy observer (spec 2026-07-17).
_SCRIPTED_PROVIDER = "scripted"
_VALID_PROVIDERS = {"local", _SCRIPTED_PROVIDER} | _CLI_PROVIDERS

@dataclass(frozen=True)
class BriefingOptions:
    enabled: bool = False
    map_radius: int = 3
    sections: tuple[str, ...] = ("overview", "units", "cities", "map", "research", "production_options")

@dataclass(frozen=True)
class MemoryOptions:
    enabled: bool = False
    max_chars: int = 1200
    max_age_turns: int = 10


@dataclass(frozen=True)
class TaskTrackerOptions:
    enabled: bool = False
    max_tasks: int = 8

@dataclass(frozen=True)
class AttentionOptions:
    """Quiet-turn attention policy (spec 2026-07-09). mode: off|auto|model|hybrid."""
    mode: str = "off"
    max_skip: int = 5        # upper clamp for a model's SKIP: n
    max_streak: int = 5      # coordinator-side consecutive-sleep cap
    threat_radius: int = 4   # hostile-scan radius around cities/civilians


@dataclass(frozen=True)
class ChannelOptions:
    enabled: bool = False
    guidance: bool = False


@dataclass(frozen=True)
class LiveGateOptions:
    """Deterministic attended live-gate scenario switch (spec 2026-07-17).

    roles is a sorted tuple of (role_name, player_id) pairs so the frozen
    dataclass stays hashable and fingerprint-stable.
    """

    enabled: bool = False
    scenario: str = ""
    roles: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ChannelRules:
    acceptance_turns: int = 3
    funding_turns: int = 2
    payment_response_turns: int = 2
    max_completion_turns: int = 30
    max_active_deals_per_pair: int = 3
    max_payment_gold: int = 10_000
    max_message_chars: int = 2_000
    max_narrative_chars: int = 1_000
    max_messages_per_pair: int = 200
    prompt_messages_per_counterpart: int = 10
    recent_terminal_deals: int = 5
    max_zone_distance: int = 10
    grievance_half_life_turns: int = 30
    prompt_grievance_threshold: float = 0.05
    max_queued_action_bytes: int = 8 * 1024

    def fingerprint(self) -> dict[str, int | float]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class CivOptions:
    tools: str | tuple = "minimal"
    result_char_cap: int = 1500
    max_steps: int = 6
    playbook: str = "none"
    context_budget: int | str = "auto"
    briefing: BriefingOptions = field(default_factory=BriefingOptions)
    memory: MemoryOptions = field(default_factory=MemoryOptions)
    task_tracker: TaskTrackerOptions = field(default_factory=TaskTrackerOptions)
    attention: AttentionOptions = field(default_factory=AttentionOptions)
    channels: ChannelOptions = field(default_factory=ChannelOptions)

    def fingerprint(self) -> dict:
        return {
            "tools": list(self.tools) if not isinstance(self.tools, str) else self.tools,
            "result_char_cap": self.result_char_cap,
            "max_steps": self.max_steps,
            "playbook": self.playbook,
            "context_budget": self.context_budget,
            "briefing": {
                "enabled": self.briefing.enabled,
                "map_radius": self.briefing.map_radius,
                "sections": list(self.briefing.sections),
            },
            "memory": {
                "enabled": self.memory.enabled,
                "max_chars": self.memory.max_chars,
                "max_age_turns": self.memory.max_age_turns,
            },
            "task_tracker": {"enabled": self.task_tracker.enabled, "max_tasks": self.task_tracker.max_tasks},
            "attention": {
                "mode": self.attention.mode,
                "max_skip": self.attention.max_skip,
                "max_streak": self.attention.max_streak,
                "threat_radius": self.attention.threat_radius,
            },
            "channels": {
                "enabled": self.channels.enabled,
                "guidance": self.channels.guidance,
            },
        }

    @property
    def standing_plan_enabled(self) -> bool:
        return self.memory.enabled or self.task_tracker.enabled

    @property
    def attention_directives_enabled(self) -> bool:
        return self.attention.mode in ("model", "hybrid")

    @property
    def _standing_plan_task_capture_chars(self) -> int:
        if not self.task_tracker.enabled:
            return 0
        extra_tasks = max(0, self.task_tracker.max_tasks - STANDING_PLAN_BASE_TASK_CAP)
        return STANDING_PLAN_CAPTURE_CHARS + (
            extra_tasks * STANDING_PLAN_CHARS_PER_EXTRA_TASK
        )

    @property
    def standing_plan_capture_chars(self) -> int:
        if not self.standing_plan_enabled:
            return 0
        capture_chars = self.memory.max_chars if self.memory.enabled else 0
        if self.task_tracker.enabled:
            capture_chars = max(capture_chars, self._standing_plan_task_capture_chars)
        return capture_chars

    @property
    def standing_plan_summary_chars(self) -> int:
        if self.standing_plan_enabled:
            return max(1200, self.standing_plan_capture_chars)
        if self.attention_directives_enabled:
            # SKIP:/WAKE IF: lines sit at the END of the final summary
            # (ATTENTION_INSTRUCTION); the plain 500-char front clamp would
            # hide them from the run-log summary field and from the
            # coordinator's raw-empty fallback path. The transcript's
            # final_summary itself is always raw -- this only widens the
            # clamped views.
            return 1200
        return 500

@dataclass(frozen=True)
class PlayerSpec:
    player_id: int
    provider: str  # "local" | "cli-claude" | "cli-codex" | "scripted" (test-only)
    model: str
    gateway: str = ""  # optional per-civ gateway override (in-process local civs only)
    options: CivOptions = field(default_factory=CivOptions)

    def driver_kind(self) -> str:
        if self.provider == _SCRIPTED_PROVIDER:
            return "scripted"
        return "cli" if self.provider in _CLI_PROVIDERS else "in_process"

def parse_player_spec(s: str) -> PlayerSpec:
    # "1:local:qwen3-coder:30b", "2:cli-claude:", "2:cli-codex:gpt-5.5", or a local civ
    # pinned to its own gateway: "3:local:gemma4-26b@http://192.168.20.196:11440/v1".
    parts = s.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"bad --player spec {s!r}; want '<id>:<provider>:<model>[@<gateway>]'")
    pid, provider, model = parts
    if provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r} in --player spec {s!r}; "
            f"want one of {sorted(_VALID_PROVIDERS)}")
    # A trailing '@<url>' pins this local civ to a specific gateway (e.g. a per-GPU
    # llama-swap instance). URLs contain ':' but not '@', so rsplit is unambiguous.
    gateway = ""
    if "@" in model:
        model, gateway = model.rsplit("@", 1)
    return PlayerSpec(int(pid), provider, model, gateway)

@dataclass
class ArenaConfig:
    players: list[PlayerSpec]
    max_puppet_turns: int = 1
    max_game_turns: int = 0  # caps ALL captured turns (played+slept+failed); 0 = uncapped
    gateway_url: str = DEFAULT_GATEWAY_URL  # overridden by CLI
    api_key_env: str = "LITELLM_OPENAI_API_KEY"
    dry_run: bool = False
    max_agent_steps: int = 6
    idle_poll_limit: int = 600
    # Seat-0 drain budgets, distinct from idle_poll_limit ("consecutive polls
    # with nothing to do"). drain: total quiet polls allowed for one admitted
    # seat-0 turn's end-fired/AI-processing drain before the run declares the
    # game hung and exits. human_pending: polls allowed for a human to resolve
    # an escalated blocker before the run exits cleanly.
    seat0_drain_poll_limit: int = 1800
    seat0_human_pending_poll_limit: int = 1800
    cost_path: str = "arena_cost.jsonl"
    puppet_ids: list[int] | None = None
    run_id: str = ""
    transcript_dir: str = "arena_runs"
    channel_rules: ChannelRules = field(default_factory=ChannelRules)
    live_gate: LiveGateOptions = field(default_factory=LiveGateOptions)


def channel_config_fingerprint(config: ArenaConfig) -> dict:
    return {
        "schema_version": 1,
        "enabled_players": sorted(
            spec.player_id for spec in config.players if spec.options.channels.enabled
        ),
        "rules": config.channel_rules.fingerprint(),
    }


def resolved_puppet_ids(config: ArenaConfig) -> list[int]:
    """Puppet seats for this config. `None` derives every configured nonzero
    seat; an explicit list (including `[]`) is authoritative once validated."""
    configured = {spec.player_id for spec in config.players}
    ids = (
        [spec.player_id for spec in config.players if spec.player_id != 0]
        if config.puppet_ids is None
        else list(config.puppet_ids)
    )
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate puppet ids {ids}")
    if 0 in ids:
        raise ValueError("seat 0 cannot appear in puppet_ids")
    unknown = sorted(set(ids) - configured)
    if unknown:
        raise ValueError(f"puppet ids are not configured players: {unknown}")
    return ids


def _validate_live_gate(config: ArenaConfig) -> None:
    gate = config.live_gate
    if type(gate.enabled) is not bool:
        raise ValueError("live_gate.enabled must be an exact boolean")
    if not gate.enabled:
        if gate.scenario or gate.roles:
            raise ValueError("disabled live_gate cannot carry a scenario or roles")
        return
    if not isinstance(gate.scenario, str) or not gate.scenario.strip():
        raise ValueError("enabled live_gate scenario must be a non-blank string")

    # Lazy imports keep config.py import-light and avoid a config/live-gate
    # dependency cycle at module import time.
    from civ_mcp.arena.live_gate import resolve_scenario
    from civ_mcp.run_id import is_safe_run_id

    meta = resolve_scenario(gate.scenario)
    try:
        role_pairs = tuple(gate.roles)
        roles = dict(role_pairs)
        supplied = [name for name, _player_id in role_pairs]
    except (TypeError, ValueError):
        raise ValueError("live_gate.roles must contain exactly the scenario roles") from None
    expected = sorted(name for name, _kind in meta.role_contracts)
    if (
        len(role_pairs) != len(expected)
        or len(roles) != len(role_pairs)
        or set(roles) != set(expected)
    ):
        raise ValueError(
            f"live_gate.roles must contain exactly {expected}, got {supplied}"
        )
    pids = list(roles.values())
    if any(type(pid) is not int for pid in pids):
        raise ValueError(f"live_gate role player ids must be exact integers, got {pids}")
    if len(pids) != len(set(pids)):
        raise ValueError(f"live_gate role player ids must be distinct, got {pids}")
    if any(spec.player_id == 0 for spec in config.players):
        # The gate relies on the human owning seat 0 across the restart
        # boundary; seat-zero piloting cannot be combined with it.
        raise ValueError("live_gate cannot be combined with a seat-zero (player 0) entry")
    specs = {
        spec.player_id: spec
        for spec in config.players
        if type(spec.player_id) is int
    }
    for role, kind in meta.role_contracts:
        pid = roles[role]
        spec = specs.get(pid)
        if spec is None:
            raise ValueError(
                f"live_gate role {role!r} player {pid} is not a configured civ"
            )
        if spec.driver_kind() != kind:
            raise ValueError(
                f"live_gate role {role!r} requires driver kind {kind!r}, "
                f"got {spec.driver_kind()!r}"
            )
        if not spec.options.channels.enabled:
            raise ValueError(
                f"live_gate role {role!r} player {pid} must be channel-enabled"
            )
        if spec.options.attention.mode != "off":
            # Turn skipping would starve the phase machine of admissions.
            raise ValueError(
                f"live_gate role {role!r} player {pid} requires attention.mode 'off'"
            )
    bound_ids = set(pids)
    unbound = sorted(
        spec.player_id
        for spec in config.players
        if spec.player_id not in bound_ids
    )
    if unbound:
        # Gate mode constructs no model-backed policies, so a configured civ
        # without a role would have no policy at all.
        raise ValueError(
            f"live_gate mode admits only gate-role civs; unbound players {unbound}"
        )
    if not is_safe_run_id(config.run_id):
        raise ValueError("live_gate requires an explicit and safe run_id")
    minimum = meta.minimum_captures(config)
    if config.max_puppet_turns < minimum:
        raise ValueError(
            f"live_gate scenario {gate.scenario!r} needs at least {minimum} puppet turns, "
            f"got {config.max_puppet_turns}"
        )
    if config.max_game_turns and config.max_game_turns < minimum:
        raise ValueError(
            f"live_gate scenario {gate.scenario!r} needs at least {minimum} game turns, "
            f"got {config.max_game_turns}"
        )


def validate_arena_config(config: ArenaConfig) -> None:
    """Raise ValueError when shared arena cross-field invariants are invalid."""
    resolved_puppet_ids(config)
    seat0 = next((spec for spec in config.players if spec.player_id == 0), None)
    if seat0 is not None and seat0.options.attention.mode != "off":
        raise ValueError(
            "seat 0 requires attention.mode 'off' for autonomous piloting"
        )
    _validate_live_gate(config)
