"""Production WSGI settings. TLS is terminated by the hosting reverse proxy."""

import os

bind = "0.0.0.0:" + os.environ.get("PORT", "3000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
preload_app = True  # Initialize schema and bootstrap admin once before forking.
timeout = 45
accesslog = None  # Avoid persisting user-supplied search queries by default.
errorlog = "-"
capture_output = True
# Do not trust arbitrary forwarded scheme headers. Secure cookies are explicit.
forwarded_allow_ips = ""
