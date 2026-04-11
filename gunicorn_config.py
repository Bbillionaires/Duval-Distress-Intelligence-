import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Worker settings
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 60
keepalive = 5

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"