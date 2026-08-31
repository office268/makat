"""השורה שהגריד מוציא.

השדות הם בדיוק מה ש-app/autodoc.py יודע לתרגם למועמד לקטלוג. שדה
שהאתר לא מסר יוצא ריק - ההחלטה מה לעשות עם חוסר היא של האימות, לא של
הגריד.
"""
import scrapy


class AutodocPart(scrapy.Item):
    part_number = scrapy.Field()   # מק"ט היצרן
    manufacturer = scrapy.Field()  # יצרן החלק (בוש, מאהלה...)
    title = scrapy.Field()         # כותרת המוצר כפי שהופיעה
    price = scrapy.Field()
    currency = scrapy.Field()
    url = scrapy.Field()           # עמוד המוצר
    image_url = scrapy.Field()
    oe_numbers = scrapy.Field()    # מק"טים מקוריים, אם נקראו מעמוד המוצר
    source = scrapy.Field()        # json-ld / html - איך נקרא
    make = scrapy.Field()          # הרכב שביקשנו, לא מה שהעמוד הציג
    model = scrapy.Field()
    part_type = scrapy.Field()
    listing_url = scrapy.Field()   # עמוד הקטגוריה שממנו הגיע
