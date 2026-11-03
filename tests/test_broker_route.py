# route() on the unified port: which engine a request's `model` names.
# Exact Strata model ids win, then flash-next/strata name patterns (coder vs general),
# everything else falls through to llama-server's router.
import unittest
from unittest.mock import patch

import gpu_broker


class RouteTests(unittest.TestCase):
    # names injected straight into route(): deterministic, no HTTP.

    def test_strata_model_id_matches_exactly(self):
        engine = gpu_broker.route('Qwen3-32B-Q4_KV', names={'strata': 'qwen3-32b-q4_kv', 'coder': ''})
        self.assertEqual(engine, 'strata')

    def test_coder_id_wins_when_both_servers_report_the_same_name(self):
        # coder is checked first in the exact-match loop (gpu_broker.py line 168).
        names = {'strata': 'shared-id', 'coder': 'shared-id'}
        self.assertEqual(gpu_broker.route('shared-id', names=names), 'coder')

    def test_flash_next_general_name_to_strata(self):
        # No Strata server reports its name (both unloaded), name pattern decides.
        engine = gpu_broker.route('Flash-Next 8B', names={'strata': '', 'coder': ''})
        self.assertEqual(engine, 'strata')

    def test_flash_next_coder_name_to_coder(self):
        engine = gpu_broker.route('flash-next-coder-14b', names={'strata': '', 'coder': ''})
        self.assertEqual(engine, 'coder')

    def test_strata_prefixed_general_name_to_strata(self):
        engine = gpu_broker.route('strata-general-v2', names={'strata': '', 'coder': ''})
        self.assertEqual(engine, 'strata')

    def test_unknown_name_falls_through_to_llama(self):
        engine = gpu_broker.route('gpt-4o-mini', names={'strata': 'qwen3-32b-q4_kv', 'coder': ''})
        self.assertEqual(engine, 'llama')

    def test_llama_model_with_a_coder_suffix_is_not_hijacked_to_coder(self):
        # The keyword branch is gated on flash-next/strata, never on a bare 'coder' in the name
        # (gpu_broker.py line 171), so a llama-server model ending in '-coder' still routes to llama.
        engine = gpu_broker.route('gemma-4-26b-a4b-coder', names={'strata': '', 'coder': ''})
        self.assertEqual(engine, 'llama')

    def test_blank_model_name_falls_through_to_llama(self):
        self.assertEqual(gpu_broker.route(None, names={'strata': '', 'coder': ''}), 'llama')

    # Case-insensitivity (route() folds at line 166: m = model.strip().lower(); names lower in the match loop):
    def test_openhands_coder_name_in_any_case_routes_to_coder(self):
        engine = gpu_broker.route('QWEN3.8-FLASH-NEXT-CODER-IQ1_M', names={'strata': '', 'coder': ''})
        self.assertEqual(engine, 'coder')

    def test_exact_strata_id_matches_in_any_case(self):
        # 'Zephyr-7B' matches no name pattern; only the exact match finds it, despite the query's case.
        self.assertEqual(gpu_broker.route('zEpHyR-7b', names={'strata': '', 'coder': 'Zephyr-7B'}), 'coder')
        # a mixed-case server id matched by a lowercase query (pattern would say 'strata' here, so also check
        # a name that only matches exactly):
        self.assertEqual(gpu_broker.route('FLASH-NEXT 8B Q4', names={'strata': 'Flash-Next 8B Q4', 'coder': ''}), 'strata')

    def test_surrounding_whitespace_and_case_are_both_folded(self):
        self.assertEqual(gpu_broker.route('  Qwen3.8-Flash-Next-Coder-IQ1_M \n', names={'strata': '', 'coder': ''}), 'coder')

    # names=None: route() asks each Strata server through ai_monitor.strata; stub it.
    def test_names_resolved_live_from_the_strata_servers(self):
        reporting = {
            8088: {'service': 'strata', 'loaded': True, 'model': 'Flash-Next 8B Q4'},
            8089: {'service': 'strata', 'loaded': False, 'model': ''},
        }
        with patch.object(gpu_broker.ai_monitor, 'strata', side_effect=lambda port: reporting[port]):
            gpu_broker._names['at'] = 0.0  # force the refresh path in strata_names()
            gpu_broker._names['strata'] = gpu_broker._names['coder'] = ''
            # strata server reports its id; pattern and exact match agree:
            self.assertEqual(gpu_broker.route('Flash-Next 8B Q4'), 'strata')
            # coder server reports a name no pattern knows: only the exact match finds it:
            gpu_broker._names['at'] = 0.0
            reporting[8089]['model'] = 'Zephyr-7B'
            self.assertEqual(gpu_broker.route('Zephyr-7B'), 'coder')
            # a known-pattern name still routes by pattern while the server reports it:
            gpu_broker._names['at'] = 0.0
            reporting[8089]['model'] = 'Flash-Next Coder'
            self.assertEqual(gpu_broker.route('Flash-Next Coder'), 'coder')
        # Leave the cached-names global as the module initializes it: no leak into other tests.
        gpu_broker._names.update({'at': 0.0, 'strata': '', 'coder': ''})


if __name__ == '__main__':
    unittest.main(verbosity=2)
