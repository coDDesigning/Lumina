FROM python:3.12-slim

# Prevent Python from writing pyc files to disk and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirement files
COPY requirements.txt .

# Install dependencies using pip (lockfile from uv)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure entrypoint is executable
RUN chmod +x entrypoint.sh

# Ensure the data directory exists for SQLite and local storage
RUN mkdir -p /app/data

# Default to self_hosted mode, can be overridden by docker-compose
ENV DEPLOYMENT_MODE=self_hosted

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
