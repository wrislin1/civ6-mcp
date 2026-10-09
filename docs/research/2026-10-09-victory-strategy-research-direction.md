# Victory strategy research direction and SystemOne hold

Date: 2026-10-09. Status: research direction and decision record, not an implementation spec.

The primary objective is an agent that selects a plausible victory condition from available
evidence, pursues it across the game, and makes turn-level decisions that advance that strategy.
The user's updated direction is to adapt the papers' strategic ideas and put the broad SystemOne
comparison on hold. Narrow uses of decision models remain possibilities, with no experiment queued.

Claude's Plan 3 Part 1 implementation continues under its existing contract. This record does not
change its scoring, tool surface, exit gates, model roster, library freeze or held-out restrictions.
The next architectural design should address persistent victory strategy and long-horizon
evaluation. It must not make a SystemOne backend or same-menu model screen a prerequisite.

## Evidence behind the hold

The following is transcribed from the user's 2026-10-09 Pokémon Emerald screen report and hold
decision. The underlying evidence files were not independently inspected or rerun in this Civ
session. Reported artifacts belong to the Emerald evidence root:

- `task13/systemone-screen-01/REPORT.md`, its `README.md`, and `run-gpu1-block.sh`.
- `SYSTEMONE-HOLD-2026-10-09.md`.

The report covers 48 runs. The seven runs blocked by the GPU fault reportedly completed at
10:56 UTC with the expected model, one attempt per answer, one cold load per block and stable
residency. Aborted files remain quarantined. These are reported verification results, not new
Civ benchmark evidence.

Each packet contains 59 fixtures. Parentheses show the declared option permutation. Gemma's
counts below are production JSON-only pass 1, as supplied in the report.

| Arm | tactical-02 acceptable | tactical-03 acceptable | Reported warm median per call |
|---|---:|---:|---:|
| Gemma, production contract | 45 (43) | 55 (54) | 0.86–0.89 s |
| Clef-Flash 9B | 47 (47) | 49 (50) | 1.13–1.26 s |
| Kev-9B | 45 (42) | 47 (46) | 1.10–1.19 s |
| Hosted Clef 27B | 53 (52) | 52 (52) | Not supplied in this summary |

Clef-Flash's reported acceptable-count differences from Gemma are +2 with an interval of
[-5, +9] and -6 with [-12, 0]. Kev's tactical-03 difference is -8 with [-16, -1]. The
report also identifies resisted attacks, unsuitable item choices, and substantial option-order
sensitivity: Kev changes choices on 30 and 20 fixtures, Clef-Flash on 7 and 13, and Gemma on
16 and 12. Laya's compact-state arm was fast at about 0.1 s but had floor quality.

Under the user's replacement criterion—similar decision quality with materially faster
execution—the tested local 9B heads supplied no deployment winner. This supports holding the
work without spending more time on prompt variants or another broad Civ selector comparison.
Gemma's frozen Emerald production contract remains the baseline in that project. This record
does not select a Civ model or infer that all decision heads will fail other workloads.

Warm latency measurements are sufficient for that resource-allocation decision. Claims that
prefill explains all latency or that only training can fix the errors require separate evidence;
they are not established Civ findings. Infrastructure smoke latencies in the server handoff use
different requests and cannot replace these workload measurements.

The tactical-04 packet reportedly has no model responses, but nine of its ten source battles
also fed tactical-03. Unqueried status alone does not establish an independent holdout. Any
future confirmation needs a source-overlap audit and an appropriate split, potentially fresh
battles. Labelling or collecting that packet is outside this Civ work.

## Strategic elements to carry forward

These are research inputs for the next design, not adopted implementation requirements.

- **Strategic planning separated from execution.**
  [Vox Deorum](https://arxiv.org/html/2512.18564v2) assigns macro strategy to an LLM and
  tactical execution to Vox Populi. For Civ VI, investigate a persistent victory strategy,
  medium-term milestones and an executor that reports progress and blockers. Existing unit
  tracking covers only a bounded part of execution; the project has not established a Civ VI
  equivalent of Vox Populi's tactical controller.
- **Consequences that support explanations.**
  [Queen](https://arxiv.org/html/2610.03695v1) combines an expert chess encoder with a
  curriculum and search-derived distillation. The near-term transfer is to check strategic
  claims and predicted consequences against observed outcomes. Training a comparable Civ
  model would require additional expert/training infrastructure; it is not the next deliverable.
- **Monitoring and follow-through.**
  [CivBench](https://arxiv.org/html/2609.02459v1) measures proactive monitoring and execution
  of stated commitments over multiple turns. Adapt those diagnostics to strategy revisions,
  milestones and actual actions. Record information supplied automatically as well as queried
  information, since our warnings and briefings change the observation protocol. Query counts
  alone are not a measure of good strategy.
- **Bounded decisions require their own evidence.**
  [JEV versus LLMs](https://arxiv.org/html/2610.06625v2) provides useful probability,
  ordering and repeatability diagnostics. Retain these methods for any future narrow decision
  task, rather than making another general selector comparison the research priority.

## Scope of the next design

Start with the existing capable chat-agent path. Define a durable strategic record containing
the chosen victory route, supporting observations, assumptions, medium-term milestones,
resource commitments, blockers and explicit reasons for revision. Early choices may be
provisional; new evidence can justify changing the route. Ordinary tactical distractions
should not silently erase it.

Turn execution should receive that record and return evidence of progress, failure or changed
conditions. Defensive or economic investments can serve a victory strategy indirectly. Evaluate
their consequences and opportunity costs; counting actions whose names match the victory type
would reward superficial alignment. Likewise, the agent's own milestone declarations must not
become an easy self-assigned primary score.

Use the existing standing-memory and task-tracker mechanisms where appropriate, while defining
how strategic commitments survive truncation, context resets and conflicting short-term needs.
Planner cadence, revision rules, milestone representation, budgets and comparison conditions
remain decisions for the architectural brainstorm. Using one model in both planning and
execution roles is a valid starting point; a multi-model system is not assumed.

The evaluation must ultimately include full games and actual victory outcomes. Short segments
can test milestone execution and responses to changing conditions. The parent roadmap's
five-to-ten-turn trials and thirty-turn finalists cannot by themselves establish pursuit of a
winning strategy over a game. Design the longer-horizon protocol separately, retaining matched
starts, complete failure evidence and the distinction between scaffold effects and model effects.

## Conditions for reconsidering a decision head

Revisit only for a concrete bounded workload with an observed need: for example, classifying
a compact event or deciding whether evidence warrants a strategic review. These are hypotheses,
not current assignments. Compare with a simple deterministic rule and the existing LLM path.
Declare acceptable error costs, a quality margin, meaningful latency or resource savings and an
independent evaluation split before testing. Similar quality plus materially better efficiency
can satisfy the user's criterion; higher accuracy is not required in addition to every other gain.

For any resumed integration, the [server handoff](../handoffs/2026-10-08-systemone-decision-servers.md)
retains all four endpoints and operational constraints. Account for Clef/Kev GPU-1 eviction,
CPU-only Von, differing probability/usage semantics, and complete pipeline cost. Server
availability is not an obligation to build the consumer. The existing same-menu and
objective-blindness requirements still apply if the broad selector experiment is ever resumed.
