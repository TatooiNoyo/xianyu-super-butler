"""卖出订单入库后回填商品标题，不覆盖已有商品资料。"""

import os
import tempfile
import unittest
from unittest import mock

from app.db_manager import DBManager
from utils.seller_order_sync import _save_order


class SellerOrderTitleBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = DBManager(os.path.join(self.tmp.name, 'orders.db'))
        self.addCleanup(self.db.close)
        self.db.save_cookie('seller', 'unb=seller', 1)
        self.parsed = {'order_id': 'order-1', 'item_id': 'item-1', 'item_title': '订单商品名', 'buy_num': 1, 'amount': '10'}

    def test_saved_order_populates_title_for_list_and_export(self):
        self.assertTrue(_save_order(self.db, 'seller', self.parsed))
        self.assertEqual(self.db.get_item_info('seller', 'item-1')['item_title'], '订单商品名')
        self.assertIsNotNone(self.db.get_order_by_id('order-1'))

    def test_existing_product_title_is_preserved(self):
        self.db.upsert_item_title('seller', 'item-1', '已维护的商品名')
        self.assertTrue(_save_order(self.db, 'seller', self.parsed))
        self.assertEqual(self.db.get_item_info('seller', 'item-1')['item_title'], '已维护的商品名')

    def test_unsaved_or_incomplete_order_does_not_create_product(self):
        db = mock.Mock()
        db.insert_or_update_order.return_value = False
        self.assertFalse(_save_order(db, 'seller', self.parsed))
        db.upsert_item_title.assert_not_called()
        self.assertFalse(_save_order(db, 'seller', {'item_title': 'missing order id'}))
        db.insert_or_update_order.assert_called_once()
        db.insert_or_update_order.return_value = True
        self.assertTrue(_save_order(db, 'seller', {'order_id': 'order-2'}))
        db.upsert_item_title.assert_not_called()

    def test_backfill_failure_does_not_mark_saved_order_failed(self):
        db = mock.Mock()
        db.insert_or_update_order.return_value = True
        db.upsert_item_title.side_effect = RuntimeError('title storage unavailable')
        self.assertTrue(_save_order(db, 'seller', self.parsed))
