import os
import tempfile
import unittest

from app.db_manager import DBManager


class DeliveryRuleScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DBManager(os.path.join(self.temp_dir.name, "rules.db"))
        self.manager.save_cookie('seller-a', 'unb=seller-a', 1)
        self.manager.save_cookie('seller-b', 'unb=seller-b', 1)
        cursor = self.manager.conn.cursor()
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('global', 'text', 'global', 1, 1)
            """
        )
        self.global_card = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('account', 'text', 'account', 1, 1)
            """
        )
        self.account_card = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('product', 'text', 'product', 1, 1)
            """
        )
        self.product_card = cursor.lastrowid
        self.manager.conn.commit()

        self.manager.create_delivery_rule("会员", self.global_card, user_id=1)
        self.manager.create_delivery_rule(
            "会员", self.account_card, user_id=1, cookie_id="seller-a"
        )
        self.manager.create_delivery_rule(
            "", self.product_card, user_id=1,
            cookie_id="seller-a", item_id="item-1"
        )

    def tearDown(self):
        self.manager.close()
        self.temp_dir.cleanup()

    def test_exact_product_rule_outranks_keyword_rules(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-a", "item-1"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.product_card])

    def test_account_rule_outranks_global_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-a", "item-2"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.account_card])

    def test_other_account_uses_global_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-b", "item-1"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.global_card])

    def test_unmatched_text_does_not_use_generic_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "普通商品", "seller-b", "item-1"
        )

        self.assertEqual(rules, [])

    def test_global_rules_do_not_match_another_users_account(self):
        self.manager.create_user('other', 'other@example.test', 'password123')
        other_id = self.manager.get_user_by_username('other')['id']
        self.manager.save_cookie('other-account', 'unb=other', other_id)
        self.assertEqual(self.manager.get_delivery_rules_for_item(
            '会员商品', 'other-account', 'item-1'
        ), [])

        card = self.manager.create_card('other card', 'text', text_content='other secret', user_id=other_id)
        self.manager.create_delivery_rule('会员', card, user_id=other_id)
        rules = self.manager.get_delivery_rules_for_item('会员商品', 'other-account', 'item-1')
        self.assertEqual([r['card_id'] for r in rules], [card])
        own_rules = self.manager.get_delivery_rules_for_item('会员商品', 'seller-b', 'item-1')
        self.assertEqual([r['card_id'] for r in own_rules], [self.global_card])

    def test_unknown_account_cannot_match_global_rules(self):
        self.assertEqual(self.manager.get_delivery_rules_for_item('会员商品', 'missing', 'item-1'), [])

    def test_foreign_cards_and_stale_account_rules_are_excluded(self):
        self.manager.create_user('other', 'other@example.test', 'password123')
        other_id = self.manager.get_user_by_username('other')['id']
        other_card = self.manager.create_card('foreign', 'text', text_content='secret', user_id=other_id)
        # 即使历史脏数据把他人卡密绑定到了自己的规则，也不能使用。
        self.manager.create_delivery_rule('', other_card, user_id=1, cookie_id='seller-a', item_id='item-2')
        rules = self.manager.get_delivery_rules_for_item('会员商品', 'seller-a', 'item-2')
        self.assertEqual([r['card_id'] for r in rules], [self.account_card])
        # 账号归属变化后，旧用户的账号规则和商品规则也必须失效。
        self.manager.save_cookie('seller-a', 'unb=seller-a', other_id)
        self.assertEqual(self.manager.get_delivery_rules_for_item('会员商品', 'seller-a', 'item-1'), [])


if __name__ == "__main__":
    unittest.main()
