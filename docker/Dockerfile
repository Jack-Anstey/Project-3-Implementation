# Python 3.13 docker image to start
FROM python:3.13-slim

# Copy requirements first, then install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the remaining files
COPY ./ ./

# Set entrypoint
ENTRYPOINT ["python", "-m", "src"]
