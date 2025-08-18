# Use a lightweight Python image
FROM python:3.9-slim

# Working directory inside container
WORKDIR /app

# Copy files to the container
COPY . /app

# Install system dependencies for Python modules if necessary
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Port (set as 5001 since the default setup in `app.py` uses this port)
EXPOSE 5001

# Default environment variables
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5001

# Run the Flask server
CMD ["python", "app.py"]
