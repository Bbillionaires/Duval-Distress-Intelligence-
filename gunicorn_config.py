import os

# Render provides $PORT
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Worker settings
workers = 1
worker_class = "sync"
worker_connections = 1000
timeout = 300  # Increased to 5 minutes for large data loads
keepalive = 5

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"
