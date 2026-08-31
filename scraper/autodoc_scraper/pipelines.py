"""ניקוי לפני הכתיבה לקובץ.

הפילטרים הכבדים - התאמה לרכב, יצרן זר, מבנה המק"ט - יושבים באפליקציה
(app/parts_discovery.validate), כי הם אותם הפילטרים שהגילוי דרך המודל
עובר. כאן רק מה ששייך לגריד עצמו: שורה בלי מק"ט, כפילות באותה הרצה,
ומחרוזות ארוכות מהעמודות בבסיס הנתונים.
"""
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

MAX_LENGTHS = {
    "part_number": 80, "manufacturer": 120, "title": 200,
    "url": 500, "image_url": 500, "listing_url": 500,
}


class CleanPartPipeline:
    def open_spider(self, spider):
        self.seen = set()

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        number = str(adapter.get("part_number") or "").strip()
        if not number:
            raise DropItem("שורה בלי מק\"ט")

        key = number.lower()
        if key in self.seen:
            raise DropItem(f"מק\"ט כפול באותה הרצה: {number}")
        self.seen.add(key)

        for field, length in MAX_LENGTHS.items():
            value = adapter.get(field)
            if isinstance(value, str):
                adapter[field] = value.strip()[:length]
        adapter["part_number"] = number[:MAX_LENGTHS["part_number"]]
        return item
