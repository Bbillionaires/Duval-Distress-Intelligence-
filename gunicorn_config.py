# Gunicorn configuration for Render
# Allows long-running uploads and requests

# Timeout for worker processes (10 minutes = 600 seconds)
# This allows large file uploads to complete
timeout = 600

# Keep-alive connections
keepalive = 5

# Worker class
worker_class = 'sync'

# Log level
loglevel = 'info'

# Access log format
accesslog = '-'
errorlog = '-'
