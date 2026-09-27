# Arena calibration campaign report

- Campaign id: builder-economy-cal-v2
- Campaign fingerprint: c479666da27b935e734ef059c044b2ecff98da79140208729e4c297b6bf75604
- Schema version: 1.0.0
- Position: builder-economy-cal-v1
- Baseline arm: minimal
- Treatment arm: standard
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6

### Model configuration

- gemma4-26b: model=gemma4-26b, endpoint_id=home-gpu0-cpp, sampling={'max_tokens': 3072, 'seed': 101, 'temperature': 0.2, 'top_p': 0.95}, chat_template_kwargs={'enable_thinking': False}
- qwen3.6-27b: model=qwen3.6-27b, endpoint_id=home-gpu0-cpp, sampling={'max_tokens': 6144, 'seed': 101, 'temperature': 0.2, 'top_p': 0.95}, chat_template_kwargs={'enable_thinking': False}

## Verdict: BLOCKED

- Reason: neither admitted model block passed (gemma4-26b=MODEL_FLOOR_NULL, qwen3.6-27b=MODEL_NULL)

## Block: gemma4-26b

- Status: COMPLETE
- Outcome: MODEL_FLOOR_NULL

### Endpoint / GPU topology

- Resolved model: gemma4-26b
- Resolved endpoint: http://192.168.20.146:11440/v1
- GPU topology: {'gpu_indexes': [0], 'host_id': 'home-llm'}

### Calibration

- Decided pairs: 1/12 (threshold 10)
- Standard wins: 1 (threshold 10)
- Median signed normalized delta: 0.0000 (threshold 0.3333)
- Sensitivity ok: False, direction ok: False, effect ok: False

| pair | baseline_normalized | treatment_normalized | signed_delta | decided |
|---|---|---|---|---|
| builder-economy-cal-v1:gemma4-26b:seed1009:9 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed101:0 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed1103:10 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed1201:11 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed211:1 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed307:2 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed401:3 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed503:4 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed601:5 | 0.2500 | 0.3333 | 0.0833 | True |
| builder-economy-cal-v1:gemma4-26b:seed701:6 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed809:7 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:gemma4-26b:seed907:8 | 0.2500 | 0.2500 | 0.0000 | False |

### Metric fidelity: OK

- Audited indices: [1, 2, 11, 12, 23, 24]

### Tie attribution: RESOLVED

- builder-economy-cal-v1:gemma4-26b:seed1009:9: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed101:0: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed1103:10: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed1201:11: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed211:1: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed307:2: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed401:3: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed503:4: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed701:6: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed809:7: model_floor (mechanical screen: nonzero_tie)
- builder-economy-cal-v1:gemma4-26b:seed907:8: model_floor (mechanical screen: nonzero_tie)

## Block: qwen3.6-27b

- Status: COMPLETE
- Outcome: MODEL_NULL

### Endpoint / GPU topology

- Resolved model: qwen3.6-27b
- Resolved endpoint: http://192.168.20.146:11440/v1
- GPU topology: {'gpu_indexes': [0], 'host_id': 'home-llm'}

### Calibration

- Decided pairs: 11/12 (threshold 10)
- Standard wins: 11 (threshold 10)
- Median signed normalized delta: 0.2917 (threshold 0.3333)
- Sensitivity ok: True, direction ok: True, effect ok: False

| pair | baseline_normalized | treatment_normalized | signed_delta | decided |
|---|---|---|---|---|
| builder-economy-cal-v1:qwen3.6-27b:seed1009:9 | 0.2500 | 0.7500 | 0.5000 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed101:0 | 0.2500 | 0.5833 | 0.3333 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed1103:10 | 0.2500 | 0.7500 | 0.5000 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed1201:11 | 0.2500 | 0.7500 | 0.5000 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed211:1 | 0.2500 | 0.5000 | 0.2500 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed307:2 | 0.2500 | 0.8333 | 0.5833 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed401:3 | 0.2500 | 0.8333 | 0.5833 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed503:4 | 0.2500 | 0.5000 | 0.2500 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed601:5 | 0.2500 | 0.5000 | 0.2500 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed701:6 | 0.2500 | 0.2500 | 0.0000 | False |
| builder-economy-cal-v1:qwen3.6-27b:seed809:7 | 0.2500 | 0.5000 | 0.2500 | True |
| builder-economy-cal-v1:qwen3.6-27b:seed907:8 | 0.2500 | 0.5000 | 0.2500 | True |

### Metric fidelity: OK

- Audited indices: [1, 2, 11, 12, 23, 24]

### Tie attribution: RESOLVED

- builder-economy-cal-v1:qwen3.6-27b:seed701:6: model_floor (mechanical screen: nonzero_tie)

## Report inputs

- admissions/gemma4-26b-attempt-001.json
- admissions/qwen3.6-27b-attempt-001.json
- blocks/gemma4-26b/audit.json
- blocks/gemma4-26b/schedule.json
- blocks/gemma4-26b/session.json
- blocks/gemma4-26b/tie_attribution.json
- blocks/gemma4-26b/trials/trial-001.json
- blocks/gemma4-26b/trials/trial-002.json
- blocks/gemma4-26b/trials/trial-003.json
- blocks/gemma4-26b/trials/trial-004.json
- blocks/gemma4-26b/trials/trial-005.json
- blocks/gemma4-26b/trials/trial-006.json
- blocks/gemma4-26b/trials/trial-007.json
- blocks/gemma4-26b/trials/trial-008.json
- blocks/gemma4-26b/trials/trial-009.json
- blocks/gemma4-26b/trials/trial-010.json
- blocks/gemma4-26b/trials/trial-011.json
- blocks/gemma4-26b/trials/trial-012.json
- blocks/gemma4-26b/trials/trial-013.json
- blocks/gemma4-26b/trials/trial-014.json
- blocks/gemma4-26b/trials/trial-015.json
- blocks/gemma4-26b/trials/trial-016.json
- blocks/gemma4-26b/trials/trial-017.json
- blocks/gemma4-26b/trials/trial-018.json
- blocks/gemma4-26b/trials/trial-019.json
- blocks/gemma4-26b/trials/trial-020.json
- blocks/gemma4-26b/trials/trial-021.json
- blocks/gemma4-26b/trials/trial-022.json
- blocks/gemma4-26b/trials/trial-023.json
- blocks/gemma4-26b/trials/trial-024.json
- blocks/qwen3.6-27b/audit.json
- blocks/qwen3.6-27b/schedule.json
- blocks/qwen3.6-27b/session.json
- blocks/qwen3.6-27b/tie_attribution.json
- blocks/qwen3.6-27b/trials/trial-001.json
- blocks/qwen3.6-27b/trials/trial-002.json
- blocks/qwen3.6-27b/trials/trial-003.json
- blocks/qwen3.6-27b/trials/trial-004.json
- blocks/qwen3.6-27b/trials/trial-005.json
- blocks/qwen3.6-27b/trials/trial-006.json
- blocks/qwen3.6-27b/trials/trial-007.json
- blocks/qwen3.6-27b/trials/trial-008.json
- blocks/qwen3.6-27b/trials/trial-009.json
- blocks/qwen3.6-27b/trials/trial-010.json
- blocks/qwen3.6-27b/trials/trial-011.json
- blocks/qwen3.6-27b/trials/trial-012.json
- blocks/qwen3.6-27b/trials/trial-013.json
- blocks/qwen3.6-27b/trials/trial-014.json
- blocks/qwen3.6-27b/trials/trial-015.json
- blocks/qwen3.6-27b/trials/trial-016.json
- blocks/qwen3.6-27b/trials/trial-017.json
- blocks/qwen3.6-27b/trials/trial-018.json
- blocks/qwen3.6-27b/trials/trial-019.json
- blocks/qwen3.6-27b/trials/trial-020.json
- blocks/qwen3.6-27b/trials/trial-021.json
- blocks/qwen3.6-27b/trials/trial-022.json
- blocks/qwen3.6-27b/trials/trial-023.json
- blocks/qwen3.6-27b/trials/trial-024.json
- campaign.json
- schedule.json

