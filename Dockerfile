# Use an official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim-bookworm

# Set environment variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing pyc files to disc
# PYTHONUNBUFFERED: Prevents Python from buffering stdout and stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    unzip \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers and their system dependencies
RUN python -m playwright install chromium --with-deps

# Copy project files
COPY . .

# Expose port 8000
EXPOSE 8000

# Set entrypoint
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Using uvicorn directly for simplicity, but gunicorn+uvicorn is better for prod
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
