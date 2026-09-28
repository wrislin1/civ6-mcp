# Arena calibration campaign report

- Campaign id: builder-posctrl-cal-v1
- Campaign fingerprint: c3dd4df02f8e35aa788d92f61512d9d4b41ffcbf7803e76b8b8d7172d2d62b19
- Schema version: 1.0.0
- Position: builder-posctrl-v1
- Baseline arm: minimal
- Treatment arm: standard
- Scorer fingerprint: 30783b592a620d3e035a268615d46170f8b53ff87dc06d281f05eaec44d3c0e6

### Model configuration

- gemma4-26b: model=gemma4-26b, endpoint_id=home-gpu0-cpp, sampling={'max_tokens': 3072, 'seed': 101, 'temperature': 0.2, 'top_p': 0.95}, chat_template_kwargs={'enable_thinking': False}
- qwen3.6-27b: model=qwen3.6-27b, endpoint_id=home-gpu0-cpp, sampling={'max_tokens': 6144, 'seed': 101, 'temperature': 0.2, 'top_p': 0.95}, chat_template_kwargs={'enable_thinking': False}

## Verdict: CALIBRATED

- Reason: at least one admitted model block passed

## Block: gemma4-26b

- Status: COMPLETE
- Outcome: PASS

### Endpoint / GPU topology

- Resolved model: gemma4-26b
- Resolved endpoint: http://192.168.20.146:11440/v1
- GPU topology: {'gpu_indexes': [0], 'host_id': 'home-llm'}

### Calibration

- Decided pairs: 12/12 (threshold 10)
- Standard wins: 12 (threshold 10)
- Median signed normalized delta: 0.7500 (threshold 0.3333)
- Sensitivity ok: True, direction ok: True, effect ok: True

| pair | baseline_normalized | treatment_normalized | signed_delta | decided |
|---|---|---|---|---|
| builder-posctrl-v1:gemma4-26b:seed1009:9 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed101:0 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed1103:10 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed1201:11 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed211:1 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed307:2 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed401:3 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed503:4 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed601:5 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed701:6 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed809:7 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:gemma4-26b:seed907:8 | 0.2500 | 1.0000 | 0.7500 | True |

### Metric fidelity: OK

- Audited indices: [1, 2, 11, 12, 23, 24]

## Block: qwen3.6-27b

- Status: COMPLETE
- Outcome: PASS

### Endpoint / GPU topology

- Resolved model: qwen3.6-27b
- Resolved endpoint: http://192.168.20.146:11440/v1
- GPU topology: {'gpu_indexes': [0], 'host_id': 'home-llm'}

### Calibration

- Decided pairs: 12/12 (threshold 10)
- Standard wins: 12 (threshold 10)
- Median signed normalized delta: 0.7500 (threshold 0.3333)
- Sensitivity ok: True, direction ok: True, effect ok: True

| pair | baseline_normalized | treatment_normalized | signed_delta | decided |
|---|---|---|---|---|
| builder-posctrl-v1:qwen3.6-27b:seed1009:9 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed101:0 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed1103:10 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed1201:11 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed211:1 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed307:2 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed401:3 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed503:4 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed601:5 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed701:6 | 0.2500 | 1.0000 | 0.7500 | True |
| builder-posctrl-v1:qwen3.6-27b:seed809:7 | 0.2500 | 0.7500 | 0.5000 | True |
| builder-posctrl-v1:qwen3.6-27b:seed907:8 | 0.2500 | 1.0000 | 0.7500 | True |

### Metric fidelity: OK

- Audited indices: [1, 2, 11, 12, 23, 24]

## Report inputs

- admissions/gemma4-26b-attempt-001.json
- admissions/qwen3.6-27b-attempt-001.json
- blocks/gemma4-26b/audit.json
- blocks/gemma4-26b/schedule.json
- blocks/gemma4-26b/session.json
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

