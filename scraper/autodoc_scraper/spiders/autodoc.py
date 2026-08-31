"""הגריד עצמו: עמוד קטגוריה אחד, ומה שיש בו.

הרצה ידנית:

    cd scraper
    scrapy crawl autodoc -a make=טויוטה -a model=COROLLA \
        -a part_type=oil_filter -O parts.json

או עם כתובת מדויקת, כשמבנה הכתובות באתר השתנה:

    scrapy crawl autodoc -a url=https://www.autodoc.co.il/... \
        -a make=טויוטה -a model=COROLLA -a part_type=oil_filter -O parts.json

מטרה אחת להרצה, ולא סריקה של האתר: המסך מפעיל אותנו פעם אחת לכל
בקשת HTTP, ו-gunicorn הורג בקשה אחרי 60 שניות.
"""
import scrapy

from ..items import AutodocPart
from ..parsers import parse_listing, parse_oe_numbers
from ..targets import listing_url


class AutodocSpider(scrapy.Spider):
    name = "autodoc"

    def __init__(self, make="", model="", part_type="", url="",
                 details="0", limit="0", **kwargs):
        super().__init__(**kwargs)
        self.make = (make or "").strip()
        self.model = (model or "").strip()
        self.part_type = (part_type or "").strip()
        self.start_url = (url or "").strip()
        # קריאת עמוד מוצר לכל שורה מכפילה את מספר הבקשות. דלוק רק כשמי
        # שמפעיל ביקש את המספרים המקוריים ומוכן לשלם עליהם בזמן.
        self.details = str(details).strip().lower() in {"1", "true", "yes"}
        try:
            self.limit = int(limit)
        except (TypeError, ValueError):
            self.limit = 0

    async def start(self):
        """נקודת הפתיחה. \u200fScrapy 2.13 ומעלה קורא לזו ולא ל-start_requests."""
        target = self.start_url or listing_url(self.make, self.model, self.part_type)
        if not target:
            # לא מנחשים כתובת. יציאה שקטה עם אפס שורות, והמסך יאמר
            # שאין מיפוי לסוג החלק הזה.
            self.logger.error(
                "אין מיפוי כתובת ל-%s %s · %s", self.make, self.model, self.part_type
            )
            return
        yield scrapy.Request(target, callback=self.parse, errback=self.on_error)

    def on_error(self, failure):
        """כשל רשת נכתב ל-stderr, ומשם הוא מגיע ליומן שבמסך."""
        self.logger.error("הבקשה נכשלה: %s", failure.value)

    def parse(self, response):
        rows = parse_listing(response.text, response.url)
        if self.limit > 0:
            rows = rows[: self.limit]
        if not rows:
            self.logger.error("לא נמצאו מוצרים בעמוד %s", response.url)

        for row in rows:
            item = self.build(row, response.url)
            if self.details and row.get("url"):
                yield response.follow(
                    row["url"], callback=self.parse_product,
                    cb_kwargs={"item": item}, errback=self.on_error,
                )
            else:
                yield item

    def parse_product(self, response, item):
        """עמוד המוצר מוסיף את המספרים המקוריים ותו לא."""
        item["oe_numbers"] = parse_oe_numbers(response.text)
        yield item

    def build(self, row, listing):
        return AutodocPart(
            part_number=row.get("part_number", ""),
            manufacturer=row.get("manufacturer", ""),
            title=row.get("title", ""),
            price=row.get("price"),
            currency=row.get("currency", ""),
            url=row.get("url", ""),
            image_url=row.get("image_url", ""),
            oe_numbers=row.get("oe_numbers") or [],
            source=row.get("source", ""),
            make=self.make,
            model=self.model,
            part_type=self.part_type,
            listing_url=listing,
        )
