import copy
import json
from pathlib import Path

import pytest
import yaml

import civ_mcp.arena.benchmark_contract as benchmark_contract
from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM, resolved_benchmark_tools
from civ_mcp.arena.benchmark_contract import (
    CalibrationRules,
    ContractVersions,
    ModelBlockConfig,
    fingerprint_identity,
    load_campaign_manifest,
    scorer_source_fingerprint,
    suite_for_block,
    tool_input_identity,
    tool_surface_identity,
    write_contract_candidate,
)
from civ_mcp.arena.benchmark_manifest import fingerprint
from civ_mcp.arena.benchmark_schedule import compile_schedule

PROVENANCE = {"base_save": "organic-base", "archive_sha256": "deadbeef" * 8}
CONTRACT = {
    "evidence_schema_version": "1.0.0",
    "predicate_schema_version": "1.0.0",
    "report_schema_version": "1.0.0",
    "scorer_fingerprint": "scorerfp",
}


def _campaign_data() -> dict:
    return {
        "campaign_id": "builder-economy-cal-v1",
        "campaign_schema_version": "1.0.0",
        "position": "builder-economy-cal-v1",
        "position_provenance": "provenance.json",
        "contracts": "contract.yaml",
        "prompt": "Assess the current turn and call finish_trial when done.",
        "models": [
            {
                "block_id": "gemma4-26b",
                "model": "gemma4-26b",
                "endpoint_id": "home-gpu0-cpp",
                "sampling": {"temperature": 0.2, "top_p": 0.95, "seed": 101, "max_tokens": 3072},
                "chat_template_kwargs": {"enable_thinking": False},
                "briefing_required": False,
            },
            {
                "block_id": "qwen3.6-27b",
                "model": "qwen3.6-27b",
                "endpoint_id": "home-gpu0-cpp",
                "sampling": {"temperature": 0.2, "top_p": 0.95, "seed": 101, "max_tokens": 6144},
                "chat_template_kwargs": {"enable_thinking": False},
                "briefing_required": False,
            },
        ],
        "arms": [
            {"arm_id": "minimal", "tools": "minimal", "options": {}},
            {"arm_id": "standard", "tools": "standard", "options": {}},
        ],
        "seeds": [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201],
        "order": "abba",
        "driver": "single_turn",
        "fresh_conversation_per_trial": True,
        "retry_policy": {"max_attempts": 1, "backoff_s": 0.0},
        "max_steps": 8,
        "result_char_cap": 4000,
        "audit_indices": [1, 2, 11, 12, 23, 24],
        "rules": {
            "pairs_per_model": 12,
            "minimum_decided_pairs": 10,
            "minimum_standard_wins": 10,
            "minimum_median_normalized_delta": 0.3333333333333333,
            "required_audits_per_arm": 3,
        },
    }


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


def _write_campaign(tmp_path: Path, mutate=None) -> Path:
    _write_json(tmp_path / "provenance.json", PROVENANCE)
    _write_yaml(tmp_path / "contract.yaml", CONTRACT)
    data = _campaign_data()
    if mutate is not None:
        mutate(data)
    return _write_yaml(tmp_path / "campaign.yaml", data)


# ---------------------------------------------------------------------------
# CampaignManifest parsing: the happy path and Plan-2 structural restrictions
# ---------------------------------------------------------------------------


def test_load_campaign_manifest_accepts_the_frozen_plan2_shape(tmp_path):
    path = _write_campaign(tmp_path)

    campaign = load_campaign_manifest(path)

    assert campaign.campaign_id == "builder-economy-cal-v1"
    assert campaign.position == "builder-economy-cal-v1"
    assert [b.model for b in campaign.models] == ["gemma4-26b", "qwen3.6-27b"]
    assert [a.arm_id for a in campaign.arms] == ["minimal", "standard"]
    assert campaign.seeds == (101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201)
    assert campaign.retry_policy.max_attempts == 1
    assert campaign.max_steps == 8
    assert isinstance(campaign.contracts, ContractVersions)
    assert isinstance(campaign.rules, CalibrationRules)
    assert Path(campaign.position_provenance).is_absolute()
    assert Path(campaign.position_provenance).is_file()


def test_suite_for_block_produces_24_local_trials_with_balanced_audits(tmp_path):
    campaign = load_campaign_manifest(_write_campaign(tmp_path))

    for block in campaign.models:
        suite = suite_for_block(campaign, block)
        assert suite.positions == (campaign.position,)
        assert suite.models == (block.model,)
        assert suite.sampling == block.sampling

        trials = compile_schedule(suite)

        assert [t.index for t in trials] == list(range(1, 25))


def test_contract_versions_require_four_nonempty_version_fields(tmp_path):
    path = _write_campaign(tmp_path)
    bad = dict(CONTRACT)
    bad["scorer_fingerprint"] = ""
    _write_yaml(tmp_path / "contract.yaml", bad)

    with pytest.raises(ValueError, match="scorer_fingerprint"):
        load_campaign_manifest(path)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda d: d["models"][0].pop("sampling"), "sampling"),
        (lambda d: d["models"][0].pop("chat_template_kwargs"), "chat_template_kwargs"),
        (lambda d: d["models"][0]["sampling"].pop("max_tokens"), "max_tokens"),
    ],
)
def test_campaign_manifest_rejects_models_without_sampling_and_chat_template_kwargs(tmp_path, mutation, match):
    path = _write_campaign(tmp_path, mutate=mutation)

    with pytest.raises(ValueError, match=match):
        load_campaign_manifest(path)


def test_campaign_manifest_rejects_nonempty_arm_options_in_plan2(tmp_path):
    def mutate(d):
        d["arms"][0]["options"] = {"tools": ["move_unit"]}

    path = _write_campaign(tmp_path, mutate=mutate)

    with pytest.raises(ValueError, match="options"):
        load_campaign_manifest(path)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda d: d["models"].pop(1), "exactly 2 model blocks"),
        (lambda d: d["models"].reverse(), "Gemma"),
        (lambda d: d["seeds"].append(9999), "exactly 12 seeds"),
        (lambda d: d.__setitem__("order", "fifo"), "'abba'"),
        (lambda d: d.__setitem__("driver", "multi_turn"), "'single_turn'"),
        (lambda d: d.__setitem__("fresh_conversation_per_trial", False), "fresh_conversation_per_trial must be true"),
        (lambda d: d.__setitem__("retry_policy", {"max_attempts": 2, "backoff_s": 0.0}), "max_attempts must be 1"),
        (lambda d: d.__setitem__("max_steps", 6), "max_steps must be 8"),
        (lambda d: d.__setitem__("audit_indices", [1, 2, 11, 12, 23]), "exactly six audit indices"),
        (lambda d: d.__setitem__("audit_indices", [1, 4, 5, 8, 9, 12]), "balanced"),
        (
            lambda d: d["arms"].__setitem__(0, {"arm_id": "minimal", "tools": "full", "options": {}}),
            "minimal.*tools=minimal.*standard.*tools=standard",
        ),
    ],
)
def test_campaign_manifest_enforces_plan2_restrictions(tmp_path, mutation, match):
    path = _write_campaign(tmp_path, mutate=mutation)

    with pytest.raises(ValueError, match=match):
        load_campaign_manifest(path)


def test_load_campaign_manifest_requires_a_readable_position_provenance_file(tmp_path):
    path = _write_campaign(tmp_path)
    (tmp_path / "provenance.json").unlink()

    with pytest.raises(ValueError, match="position_provenance"):
        load_campaign_manifest(path)


# ---------------------------------------------------------------------------
# The objective-blind prompt fingerprint: one position, one frozen digest
# ---------------------------------------------------------------------------


def test_objective_blind_prompt_has_one_frozen_digest_for_every_position(tmp_path):
    path = _write_campaign(tmp_path)
    campaign = load_campaign_manifest(path)
    assert campaign.position == "builder-economy-cal-v1"

    digest_a = fingerprint({"system": BENCHMARK_SYSTEM, "user": campaign.prompt})
    reloaded = load_campaign_manifest(path)
    digest_b = fingerprint({"system": BENCHMARK_SYSTEM, "user": reloaded.prompt})

    assert digest_a == digest_b

    mutated = load_campaign_manifest(
        _write_campaign(tmp_path, mutate=lambda d: d.__setitem__("prompt", d["prompt"] + " extra"))
    )
    digest_c = fingerprint({"system": BENCHMARK_SYSTEM, "user": mutated.prompt})

    assert digest_c != digest_a


# ---------------------------------------------------------------------------
# Tool identity: surface (names + capability IDs) vs input (full schemas)
# ---------------------------------------------------------------------------


def test_tool_surface_fingerprint_ignores_schema_text_changes():
    minimal_tools = resolved_benchmark_tools("minimal")
    fp_before = fingerprint_identity(tool_surface_identity({"minimal": minimal_tools}))

    mutated_tools = copy.deepcopy(minimal_tools)
    for schema in mutated_tools:
        schema["function"]["description"] = schema["function"]["description"] + " MUTATED DESCRIPTION"

    fp_after = fingerprint_identity(tool_surface_identity({"minimal": mutated_tools}))

    assert fp_before == fp_after


def test_tool_input_fingerprint_changes_when_description_or_parameters_change():
    minimal_tools = resolved_benchmark_tools("minimal")
    fp_before = fingerprint_identity(tool_input_identity({"minimal": minimal_tools}))

    mutated_tools = copy.deepcopy(minimal_tools)
    target = next(
        t
        for t in mutated_tools
        if t["function"]["name"] != "finish_trial" and t["function"]["parameters"]["required"]
    )
    required_name = target["function"]["parameters"]["required"][0]
    target["function"]["parameters"]["properties"][required_name]["description"] = "MUTATED NESTED ARG"

    fp_after = fingerprint_identity(tool_input_identity({"minimal": mutated_tools}))

    assert fp_before != fp_after
    # The same mutation must NOT move the surface identity/fingerprint.
    surface_before = fingerprint_identity(tool_surface_identity({"minimal": minimal_tools}))
    surface_after = fingerprint_identity(tool_surface_identity({"minimal": mutated_tools}))
    assert surface_before == surface_after


def test_tool_identity_fails_if_resolved_schema_exposes_end_turn():
    tools = [t for t in resolved_benchmark_tools("minimal") if t["function"]["name"] != "finish_trial"]
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "end_turn",
                "description": "advance the turn",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    )

    with pytest.raises(ValueError, match="end_turn"):
        tool_surface_identity({"minimal": tools})
    with pytest.raises(ValueError, match="end_turn"):
        tool_input_identity({"minimal": tools})


def test_tool_identity_fails_if_resolved_schema_omits_finish_trial():
    tools = [t for t in resolved_benchmark_tools("minimal") if t["function"]["name"] != "finish_trial"]

    with pytest.raises(ValueError, match="finish_trial"):
        tool_surface_identity({"minimal": tools})
    with pytest.raises(ValueError, match="finish_trial"):
        tool_input_identity({"minimal": tools})


def test_tool_surface_identity_carries_ordered_names_and_capability_ids():
    tools = resolved_benchmark_tools("standard")

    identity = tool_surface_identity({"standard": tools})

    assert [entry["name"] for entry in identity["standard"]] == [t["function"]["name"] for t in tools]
    assert identity["standard"][-1] == {"name": "finish_trial", "capability_id": None}
    assert all("description" not in entry and "parameters" not in entry for entry in identity["standard"])


# ---------------------------------------------------------------------------
# Scorer source fingerprint and the atomic contract-candidate writer
# ---------------------------------------------------------------------------


def _make_fake_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    arena_dir = repo_root / "src" / "civ_mcp" / "arena"
    arena_dir.mkdir(parents=True)
    (arena_dir / "action_metrics.py").write_text("# action metrics v1\n")
    (arena_dir / "benchmark_report.py").write_text("# benchmark report v1\n")
    (arena_dir / "benchmark_campaign_report.py").write_text("# campaign report v1\n")
    return repo_root


def test_scorer_source_fingerprint_changes_with_file_bytes(tmp_path):
    repo_root = _make_fake_repo(tmp_path)
    fp_before = scorer_source_fingerprint(repo_root)

    (repo_root / "src" / "civ_mcp" / "arena" / "action_metrics.py").write_text("# changed\n")
    fp_after = scorer_source_fingerprint(repo_root)

    assert fp_before != fp_after


def test_scorer_source_fingerprint_reports_the_missing_file_by_name(tmp_path):
    repo_root = _make_fake_repo(tmp_path)
    (repo_root / "src" / "civ_mcp" / "arena" / "benchmark_campaign_report.py").unlink()

    with pytest.raises(ValueError, match="benchmark_campaign_report.py"):
        scorer_source_fingerprint(repo_root)


def test_write_contract_candidate_writes_atomically_and_returns_payload(tmp_path):
    repo_root = _make_fake_repo(tmp_path)
    scorer_fp = scorer_source_fingerprint(repo_root)
    versions = ContractVersions(
        evidence_schema_version="1.0.0",
        predicate_schema_version="1.0.0",
        report_schema_version="1.0.0",
        scorer_fingerprint=scorer_fp,
    )
    out_path = tmp_path / "contracts" / "candidate.yaml"

    payload = write_contract_candidate(out_path, versions, repo_root)

    assert out_path.is_file()
    on_disk = yaml.safe_load(out_path.read_text())
    assert on_disk == payload
    assert payload["scorer_fingerprint"] == scorer_fp
    assert not any(p.name.startswith(".") for p in out_path.parent.iterdir())


def test_write_contract_candidate_rejects_a_stale_scorer_fingerprint(tmp_path):
    repo_root = _make_fake_repo(tmp_path)
    versions = ContractVersions(
        evidence_schema_version="1.0.0",
        predicate_schema_version="1.0.0",
        report_schema_version="1.0.0",
        scorer_fingerprint="stale-value-from-a-different-checkout",
    )

    with pytest.raises(ValueError, match="scorer_fingerprint"):
        write_contract_candidate(tmp_path / "candidate.yaml", versions, repo_root)


def test_freeze_cli_writes_candidate_with_the_computed_scorer_fingerprint(tmp_path, monkeypatch):
    repo_root = _make_fake_repo(tmp_path)
    out_path = tmp_path / "candidate.yaml"
    monkeypatch.setattr(benchmark_contract, "_repo_root", lambda: repo_root)

    exit_code = benchmark_contract.main(
        [
            "freeze",
            "--evidence-version",
            "1.0.0",
            "--predicate-version",
            "1.0.0",
            "--report-version",
            "1.0.0",
            "--output",
            str(out_path),
        ]
    )

    assert exit_code == 0
    payload = yaml.safe_load(out_path.read_text())
    assert payload["evidence_schema_version"] == "1.0.0"
    assert payload["scorer_fingerprint"] == scorer_source_fingerprint(repo_root)
