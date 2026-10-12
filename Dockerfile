FROM python:3.12-slim

# Set environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    U2NET_HOME=/app/models

WORKDIR /app

# Install system dependencies
# - libgl1, libglib2.0-0: Required by OpenCV
# - libmagic1: Required by python-magic
# - poppler-utils: Required by pdfplumber/pdfminer
# - font-config: Required for robust font rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    poppler-utils \
    ghostscript \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libfreetype6-dev \
    fontconfig \
    default-jre-headless \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto \
    fonts-sil-abyssinica \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download rembg AI models to avoid slow downloads at runtime
RUN python3 -c 'from rembg import new_session; new_session("u2net")'

# Copy the rest of the application
COPY . .

# Expose the port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]