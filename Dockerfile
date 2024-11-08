FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml poetry.lock* /app/

RUN pip install poetry && poetry install --without dev
COPY . /app


CMD ["poetry", "run", "mqtt-telegram" ]
