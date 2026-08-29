"""הגדרות gunicorn לפרודקשן."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
threads = int(os.environ.get("WEB_THREADS", 4))
worker_class = "gthread"
timeout = int(os.environ.get("WEB_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5

# Railway אוסף את מה שנכתב ל-stdout/stderr
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
# בלי זה כל שורת גישה תציג את ה-IP של ה-proxy במקום של המשתמש
forwarded_allow_ips = "*"
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(D)sµs'

preload_app = True
