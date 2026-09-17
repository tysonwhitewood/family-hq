import unittest
from datetime import date

import kids


class SettingsTests(unittest.TestCase):
    def test_defaults_include_the_three_children(self):
        settings = kids.kids_settings({})
        keys = [c['key'] for c in settings['children']]
        self.assertEqual(keys, ['maia', 'annaliese', 'tj'])
        self.assertEqual(settings['timezone'], 'Australia/Brisbane')
        self.assertEqual(settings['money_meal_weekday'], 6)
        self.assertEqual(settings['money_meal_hour'], 16)
        maia = kids.child_by_key(settings, 'maia')
        self.assertEqual(maia['age'], 12)
        self.assertIn('grow', maia['jars'])
        tj = kids.child_by_key(settings, 'TJ')
        self.assertEqual(tj['name'], 'TJ')
        self.assertTrue(tj['split_locked'])
        self.assertIsNone(kids.child_by_key(settings, 'nope'))

    def test_config_overlays_channel_and_split_without_dropping_defaults(self):
        settings = kids.kids_settings({
            'kids': {
                'bonus_cap': 3,
                'children': [
                    {'key': 'maia', 'mattermost_channel_id': 'chan-maia', 'split': {'splurge': 0.3, 'smile': 0.5, 'give': 0.1, 'grow': 0.1}},
                ],
            }
        })
        self.assertEqual(settings['bonus_cap'], 3)
        maia = kids.child_by_key(settings, 'maia')
        self.assertEqual(maia['mattermost_channel_id'], 'chan-maia')
        self.assertEqual(maia['name'], 'Maia')
        self.assertEqual(maia['split']['smile'], 0.5)


class PinTests(unittest.TestCase):
    def test_four_digit_pin_round_trips(self):
        hashed = kids.hash_pin('2468')
        self.assertTrue(kids.pin_ok('2468', hashed))
        self.assertFalse(kids.pin_ok('0000', hashed))
        self.assertFalse(kids.pin_ok('2468', ''))
        self.assertFalse(kids.pin_ok('246', hashed))

    def test_pin_must_be_four_digits(self):
        with self.assertRaises(ValueError):
            kids.hash_pin('12')
        with self.assertRaises(ValueError):
            kids.hash_pin('abcd')


class MoneyTests(unittest.TestCase):
    def test_split_pay_remainder_lands_on_the_last_jar(self):
        child = kids.child_by_key(kids.kids_settings({}), 'tj')
        parts = kids.split_pay(10, child)
        self.assertEqual(parts['splurge'], 4.0)
        self.assertEqual(parts['smile'], 5.0)
        self.assertEqual(parts['give'], 1.0)
        self.assertEqual(round(sum(parts.values()), 2), 10)

    def test_split_pay_one_cent_remainder_does_not_vanish(self):
        child = {
            'jars': ['splurge', 'smile', 'give'],
            'split': {'splurge': 1 / 3, 'smile': 1 / 3, 'give': 1 / 3},
        }
        parts = kids.split_pay(1, child)
        self.assertEqual(kids.round_cents(sum(parts.values())), 1.0)

    def test_family_bonus_is_one_percent_capped(self):
        self.assertEqual(kids.family_bonus(100, 0, 0.01, 2), 1.0)
        self.assertEqual(kids.family_bonus(150, 100, 0.01, 2), 2.0)
        self.assertEqual(kids.family_bonus(0, 0, 0.01, 2), 0.0)
        self.assertEqual(kids.family_bonus(-5, 10, 0.01, 2), 0.1)

    def test_credit_jars_adds_parts(self):
        out = kids.credit_jars({'splurge': 1, 'smile': 2, 'give': 0, 'grow': 0}, {'smile': 1.08})
        self.assertEqual(out['smile'], 3.08)
        self.assertEqual(out['splurge'], 1)

    def test_maia_bonus_jar_is_grow_others_smile(self):
        settings = kids.kids_settings({})
        self.assertEqual(kids.bonus_jar(kids.child_by_key(settings, 'maia')), 'grow')
        self.assertEqual(kids.bonus_jar(kids.child_by_key(settings, 'tj')), 'smile')


class PrincipleTests(unittest.TestCase):
    def test_age_bands_match_kit_playbooks(self):
        settings = kids.kids_settings({})
        tj = {p['id'] for p in kids.principles_for(kids.child_by_key(settings, 'tj'))}
        anna = {p['id'] for p in kids.principles_for(kids.child_by_key(settings, 'annaliese'))}
        maia = {p['id'] for p in kids.principles_for(kids.child_by_key(settings, 'maia'))}
        self.assertIn('coins', tj)
        self.assertNotIn('compounding', tj)
        self.assertIn('sleep-on-it', anna)
        self.assertNotIn('coins', anna)
        self.assertIn('compounding', maia)
        self.assertIn('scams', maia)
        self.assertNotIn('coins', maia)

    def test_next_principle_skips_completed(self):
        child = kids.child_by_key(kids.kids_settings({}), 'tj')
        first = kids.next_principle(child, [])
        self.assertEqual(first['id'], 'coins')
        second = kids.next_principle(child, {'coins'})
        self.assertEqual(second['id'], 'need-want')
        all_ids = {p['id'] for p in kids.principles_for(child)}
        self.assertIsNone(kids.next_principle(child, all_ids))


class CommandTests(unittest.TestCase):
    def test_kid_commands(self):
        self.assertEqual(kids.parse_kid_command('Jars'), {'action': 'jars'})
        self.assertEqual(kids.parse_kid_command('balance'), {'action': 'jars'})
        self.assertEqual(kids.parse_kid_command('goal'), {'action': 'goal'})
        self.assertEqual(kids.parse_kid_command('help'), {'action': 'help'})
        self.assertIsNone(kids.parse_kid_command('gst 9262'))
        self.assertIsNone(kids.parse_kid_command('paid'))

    def test_parent_jars_labelled_and_bare_numbers(self):
        maia = kids.child_by_key(kids.kids_settings({}), 'maia')
        labelled = kids.parse_parent_jars('jars maia splurge 12.40 smile 48 give 6.20 grow 101', maia)
        self.assertEqual(labelled['jars']['grow'], 101.0)
        self.assertEqual(labelled['jars']['splurge'], 12.4)
        bare = kids.parse_parent_jars('jars maia 12.4 48 6.2 101')
        self.assertEqual(bare['key'], 'maia')
        self.assertEqual(bare['jars']['smile'], 48.0)
        self.assertIsNone(kids.parse_parent_jars('jars maia 12.4 48 6.2 101', kids.child_by_key(kids.kids_settings({}), 'tj')))


class ChannelSafetyTests(unittest.TestCase):
    def test_refuses_family_finance_even_if_misconfigured_as_the_child_channel(self):
        child = {'mattermost_channel_id': 'finance-chan'}
        self.assertFalse(kids.posting_allowed('finance-chan', child, 'finance-chan'))

    def test_allows_only_the_child_channel(self):
        child = {'mattermost_channel_id': 'kids-maia'}
        self.assertTrue(kids.posting_allowed('kids-maia', child, 'finance-chan'))
        self.assertFalse(kids.posting_allowed('kids-tj', child, 'finance-chan'))
        self.assertFalse(kids.posting_allowed('', child, 'finance-chan'))
        self.assertFalse(kids.posting_allowed('kids-maia', {'mattermost_channel_id': ''}, 'finance-chan'))


class CopyTests(unittest.TestCase):
    def test_money_meal_uses_the_child_name_and_family_bonus(self):
        maia = kids.child_by_key(kids.kids_settings({}), 'maia')
        jars = {'splurge': 12.4, 'smile': 48, 'give': 6.2, 'grow': 101.08}
        goal = {'title': 'Lego', 'target_amount': 45}
        text = kids.compose_money_meal(maia, jars, 1.08, goal, {'title': 'Why waiting beats tapping'}, [])
        self.assertIn('Hi Maia.', text)
        self.assertIn('Grow $101.08', text)
        self.assertIn('Family bonus this week: $1.08', text)
        self.assertIn('you are there. Nice', text)
        self.assertIn('Next principle: Why waiting beats tapping.', text)
        self.assertIn('Money Meal is at 4pm.', text)
        self.assertNotIn('allowance', text.lower())

    def test_parent_digest_lists_kit_transfers(self):
        text = kids.compose_parent_digest(
            [{'child_key': 'tj', 'amount': 1.0, 'note': 'family bonus', 'kind': 'bonus'}],
            kids.kids_settings({})['children'],
        )
        self.assertIn('TJ: $1.00 — family bonus', text)
        self.assertIn('pay into kit', text.lower())

    def test_decide_on_date_is_the_next_sunday_not_today(self):
        self.assertEqual(kids.decide_on_date(date(2026, 9, 18)), date(2026, 9, 20))  # Friday
        self.assertEqual(kids.decide_on_date(date(2026, 9, 20)), date(2026, 9, 27))  # Sunday → following


if __name__ == '__main__':
    unittest.main()
