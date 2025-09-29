FROM ghcr.io/astral-sh/uv:python3.13-alpine

WORKDIR /app

ADD pyproject.toml uv.lock /app/

RUN uv sync

ADD . /app/

CMD ["uv", "run", "tiger_agent", "run"]
