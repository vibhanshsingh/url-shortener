# Pinned, not "latest" — reproducibility matters more than convenience.
FROM python:3.12-slim

# Prevents Python from writing .pyc files and buffering stdout —
# buffered logs are a real pain when you're tailing `docker logs` in prod.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only requirements first. Docker caches layers by content hash —
# as long as requirements.txt doesn't change, this layer (and the slow
# `pip install`) is reused on every rebuild, even after you edit app code.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the application code.
COPY ./app ./app

# Tests and pytest configuration need to be available inside the image
# for `docker compose exec api pytest ...` to discover and run them.
COPY pytest.ini .
COPY ./tests ./tests

# Alembic needs its config file and migration scripts inside the image
# too — these were missed originally, which is exactly why `alembic
# upgrade head` failed with "no config file 'alembic.ini' found": the
# file simply wasn't in the container.
COPY alembic.ini .
COPY ./migrations ./migrations

EXPOSE 8000

# --reload is for local dev only. We'll swap this for a Gunicorn +
# Uvicorn worker setup in Milestone 15 (Production Improvements).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
