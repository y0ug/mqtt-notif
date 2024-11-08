FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml poetry.lock* /app/

# Install deps first
RUN pip install poetry && poetry install  --no-root --without dev

# Copy application and install it
# this avoid downloading deps on every application code change
COPY . /app

RUN poetry install --only-root

CMD ["poetry", "run", "mqtt-notif" ]
