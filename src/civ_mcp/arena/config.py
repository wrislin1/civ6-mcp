from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PlayerSpec:
    player_id: int
    provider: str  # "local" | "anthropic" | "openai"
    model: str

def parse_player_spec(s: str) -> PlayerSpec:
    # "1:local:qwen3-coder-30b"
    parts = s.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"bad --player spec {s!r}; want '<id>:<provider>:<model>'")
    pid, provider, model = parts
    return PlayerSpec(int(pid), provider, model)

@dataclass
class ArenaConfig:
    players: list[PlayerSpec]
    max_puppet_turns: int = 1
    gateway_url: str = "http://192.168.20.146:4000/v1"  # placeholder; overridden by CLI
    api_key_env: str = "LITELLM_OPENAI_API_KEY"
    dry_run: bool = False
    max_agent_steps: int = 6
    cost_path: str = "arena_cost.jsonl"
    puppet_ids: list[int] = field(default_factory=list)
