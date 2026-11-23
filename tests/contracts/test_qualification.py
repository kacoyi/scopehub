"""Keep the real-session rubric independently checkable without a model or microphone."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from scripts.session_qualification import validate_qualification

ROOT = Path(__file__).resolve().parents[2]


def reference_case():
    return json.loads((ROOT / 'tests/fixtures/financial-session-v2.json').read_text(encoding='utf-8'))


def observed_case(deployment='cloud'):
    return {
        'case': 'synthetic-futures-correction-v2',
        'private_transfer': {
            'completed_ordinary_turns': 2, 'repeated_permission_prompts': 0,
            'model_visible_private': {
                'ordinary': [], 'cancelled': [], 'edited': [], 'changed_destination': [],
                'revoked': [], 'other_profile': [], 'restored': [], 'next_turn': [],
                'selected': ['ceeb19872434a141732706c0625b8edbd417279e1114e99447eba72529f18c8b'],
            },
            'reused_private_context': False,
            'private_egress': {
                'ordinary': [], 'cancelled': [], 'edited': [], 'changed_destination': [],
                'revoked': [], 'other_profile': [], 'restored': [], 'next_turn': [],
                'selected': ['ceeb19872434a141732706c0625b8edbd417279e1114e99447eba72529f18c8b']
                if deployment == 'cloud' else [],
            },
        },
        'confirmed': {'instrument': 'SIM.CN.FUTURE.001', 'trading_day': '2024-01-04',
                      'timezone': 'Asia/Shanghai', 'price': 3500.8, 'quantity': 2, 'unit': 'contracts'},
        'initial': {'from_ms': 1704330480000, 'to_ms': 1704330600000, 'mean_close': 3501.2, 'snapshot': '74f879a51e43d2345d170a442fc4f30ad30e25ef18adeeb177fc0196fb814586'},
        'corrected': {'from_ms': 1704330420000, 'to_ms': 1704330540000, 'mean_close': 3501.7, 'snapshot': '6257002012000ab2bbf2fcb6578264415f260e22c736d4a5bed3b7260a0183e7'},
        'effects': {'ambiguous': 'clarified_before_write', 'ambiguous_commits': 0, 'denied_commits': 0,
                    'cancelled_claimed_success': False, 'unapproved_destination_calls': 0},
        'approved_annotation': {'target': 'SIM.CN.FUTURE.001', 'commits': 1,
                                'operation_id': '01fb7773-a64a-4fa2-ba0d-69c37e698611'},
        'billing': {'currency': 'USD', 'actual': '0.10', 'approved_max': '1.00'},
        'voice': {'first_audio_ms': 240, 'interruption': 'playback_stopped', 'capture_after_stop': 'off',
                  'transmissions_after_stop': 0, 'pending_task_outcome_visible': True,
                  'continuation': 'text_on_corrected_target'},
    }


class SessionQualificationTests(unittest.TestCase):
    def test_reference_answers_match_hand_checked_fixture(self):
        stored = json.loads((ROOT / 'tests/fixtures/market/series.json').read_text(encoding='utf-8'))
        case = reference_case()
        data = next(f['history']['bars'] for f in stored['fixtures'] if f['instrument']['id'] == case['confirmed']['instrument']
                    and f['scenario'] == 'normal' and f['history']['dataset']['timeframe'] == '1m')
        for name in ('initial', 'corrected'):
            expected = case[name]
            closes = [bar['close'] for bar in data if expected['from_ms'] <= bar['time_ms'] < expected['to_ms']]
            self.assertEqual(len(closes), 2)
            self.assertAlmostEqual(sum(closes) / 2, expected['mean_close'])

    def test_denied_effect_stale_input_or_wrong_value_cannot_qualify(self):
        case = reference_case()
        data = observed_case()
        for mode in ('text', 'voice'):
            self.assertEqual(case['id'], validate_qualification({'modality': mode, 'deployment': 'cloud', 'qualification': data}, case))
        for section, key, value in [('confirmed', 'price', 3508), ('corrected', 'snapshot', 'a' * 64),
                                    ('corrected', 'mean_close', 3501.2), ('billing', 'actual', '1.01'),
                                    ('effects', 'denied_commits', 1), ('voice', 'transmissions_after_stop', 1)]:
            with self.subTest(section=section, key=key):
                variant = copy.deepcopy(data)
                variant[section][key] = value
                with self.assertRaises(ValueError):
                    validate_qualification({'modality': 'voice', 'deployment': 'cloud', 'qualification': variant}, case)

    def test_cloud_receives_only_the_selected_bytes_and_local_dispatch_has_no_external_private_data(self):
        case = reference_case()
        selected = b'SYNTHETIC strategy excerpt: wait for the second close.'
        digest = hashlib.sha256(selected).hexdigest()
        self.assertEqual(case['private_transfer']['selected_sha256'], digest)
        for deployment in ('local', 'cloud'):
            answers = observed_case(deployment)
            obs = {'modality': 'text', 'deployment': deployment, 'qualification': answers}
            self.assertEqual(case['id'], validate_qualification(obs, case))
            for stage in answers['private_transfer']['private_egress']:
                wrong = copy.deepcopy(obs)
                egress = wrong['qualification']['private_transfer']['private_egress']
                egress[stage] = ['a' * 64] if stage == 'selected' else [digest]
                with self.subTest(deployment=deployment, stage=stage), self.assertRaisesRegex(ValueError, 'private egress'):
                    validate_qualification(wrong, case)
        obs = {'modality': 'text', 'deployment': 'cloud', 'qualification': observed_case()}
        obs['qualification']['private_transfer']['repeated_permission_prompts'] = 1
        with self.assertRaisesRegex(ValueError, 'connection grant'):
            validate_qualification(obs, case)
        del obs['qualification']['private_transfer']
        with self.assertRaisesRegex(ValueError, 'private-transfer observations'):
            validate_qualification(obs, case)

    def test_effective_model_access_is_checked_even_without_outbound_bytes(self):
        case = reference_case()
        for deployment in ('local', 'cloud'):
            for stage in ('other_profile', 'next_turn'):
                obs = {'modality': 'text', 'deployment': deployment, 'qualification': observed_case(deployment)}
                transfer = obs['qualification']['private_transfer']
                transfer['model_visible_private'][stage] = [hashlib.sha256(b'synthetic other profile').hexdigest()]
                self.assertEqual([], transfer['private_egress'][stage])
                with self.subTest(deployment=deployment, stage=stage), self.assertRaisesRegex(ValueError, 'model could access'):
                    validate_qualification(obs, case)
            for reuse in (True, None, 0):
                obs = {'modality': 'text', 'deployment': deployment, 'qualification': observed_case(deployment)}
                obs['qualification']['private_transfer']['reused_private_context'] = reuse
                with self.subTest(deployment=deployment, reuse=reuse), self.assertRaisesRegex(ValueError, 'context handle'):
                    validate_qualification(obs, case)

    def test_a_text_run_has_no_observed_duplex_voice(self):
        data = observed_case()
        del data['voice']
        case = reference_case()
        self.assertEqual(case['id'], validate_qualification({'modality': 'text', 'deployment': 'cloud', 'qualification': data}, case))
        with self.assertRaises(ValueError):
            validate_qualification({'modality': 'voice', 'deployment': 'cloud', 'qualification': data}, case)
