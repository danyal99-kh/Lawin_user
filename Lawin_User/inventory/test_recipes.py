import json
from decimal import Decimal

from django.test import Client, TestCase

from catalog.models import Product
from catalog.services import is_available
from core.testing import make_world
from inventory.models import Recipe, RecipeItem


class RecipeApiTests(TestCase):
    def setUp(self):
        self.w = (
            make_world()
        )  # latte: شیر ۲۰۰ml + قهوه ۱۸g | cake بدون دستور | off غیرفعال
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def put(self, pid, body):
        return self.api.put(
            f"/api/v1/products/{pid}/recipe/",
            json.dumps(body),
            content_type="application/json",
        )

    def test_requires_token(self):
        self.assertEqual(Client().get("/api/v1/recipes/").status_code, 401)
        self.assertEqual(
            Client()
            .put(
                f"/api/v1/products/{self.w.latte.id}/recipe/",
                "{}",
                content_type="application/json",
            )
            .status_code,
            401,
        )

    def test_list_includes_all_products_even_without_recipe(self):
        r = self.api.get("/api/v1/recipes/")
        self.assertEqual(r.status_code, 200)
        by = {x["product_id"]: x for x in r.json()}
        self.assertEqual(len(by), 3)
        self.assertEqual(by[self.w.cake.id]["items"], [])
        self.assertEqual(by[self.w.cake.id]["product_name"], "کیک")
        items = {i["inventory_item_id"]: i for i in by[self.w.latte.id]["items"]}
        self.assertEqual(items[self.w.milk.id]["quantity"], 200.0)
        self.assertIsInstance(items[self.w.milk.id]["quantity"], float)
        self.assertEqual(items[self.w.milk.id]["unit"], "ml")
        self.assertEqual(items[self.w.milk.id]["inventory_item_name"], "شیر")
        self.assertEqual(items[self.w.beans.id]["unit"], "g")

    def test_put_replaces_whole_recipe(self):
        r = self.put(
            self.w.latte.id,
            {"items": [{"inventory_item_id": self.w.beans.id, "quantity": 20.5}]},
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()["items"]), 1)
        self.assertEqual(r.json()["items"][0]["quantity"], 20.5)
        ri = RecipeItem.objects.get(recipe__product=self.w.latte)
        self.assertEqual(
            (ri.inventory_item_id, ri.quantity), (self.w.beans.id, Decimal("20.5"))
        )

    def test_put_creates_recipe_for_product_without_one(self):
        r = self.put(
            self.w.cake.id,
            {"items": [{"inventory_item_id": self.w.milk.id, "quantity": "30"}]},
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Recipe.objects.get(product=self.w.cake).items.count(), 1)

    def test_put_empty_clears_recipe(self):
        r = self.put(self.w.latte.id, {"items": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"], [])
        self.assertFalse(Recipe.objects.filter(product=self.w.latte).exists())
        self.assertTrue(is_available(Product.objects.get(pk=self.w.latte.id)))

    def test_duplicate_item_rejected_and_old_recipe_kept(self):
        r = self.put(
            self.w.latte.id,
            {
                "items": [
                    {"inventory_item_id": self.w.milk.id, "quantity": 1},
                    {"inventory_item_id": self.w.milk.id, "quantity": 2},
                ]
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertEqual(
            RecipeItem.objects.filter(recipe__product=self.w.latte).count(), 2
        )

    def test_bad_quantities_rejected(self):
        for q in (0, -5, "abc", True, None, "NaN", "1e20", 0.0004, [1]):
            r = self.put(
                self.w.latte.id,
                {"items": [{"inventory_item_id": self.w.milk.id, "quantity": q}]},
            )
            self.assertEqual(r.status_code, 400, q)
        self.assertEqual(
            RecipeItem.objects.filter(recipe__product=self.w.latte).count(), 2
        )

    def test_bad_payloads_rejected(self):
        for body in (
            {},
            {"items": "x"},
            {"items": [1]},
            {"items": [{"quantity": 1}]},
            {"items": [{"inventory_item_id": True, "quantity": 1}]},
            {"items": [{"inventory_item_id": 999999, "quantity": 1}]},
        ):
            self.assertEqual(self.put(self.w.latte.id, body).status_code, 400, body)

    def test_unknown_product_404(self):
        r = self.put(999999, {"items": []})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"))

    def test_recipe_change_affects_availability(self):
        self.assertTrue(is_available(Product.objects.get(pk=self.w.latte.id)))
        self.put(
            self.w.latte.id,
            {"items": [{"inventory_item_id": self.w.beans.id, "quantity": 1000}]},
        )  # موجودی ۵۰۰
        self.assertFalse(is_available(Product.objects.get(pk=self.w.latte.id)))
