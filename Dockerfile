FROM python:3.8-slim as builder

# Build

RUN apt-get update \
 && apt-get install -y gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR app
COPY requirements.txt ./
RUN pip install --user --trusted-host pypi.python.org -r requirements.txt
COPY . /app


FROM python:3.8-slim as app

COPY --from=builder /root/.local /root/.local

# Set metadata
ARG PARA_ID
ARG INITIAL_BLOCK_NUMBER
ARG ERA_DURATION

ENV PARA_ID=${PARA_ID:-999}
ENV ABI_PATH=./assets/oracle.json
ENV INITIAL_BLOCK_NUMBER=${INITIAL_BLOCK_NUMBER:-1}
ENV ERA_DURATION=${ERA_DURATION:-30}
ENV PATH=/root/.local/bin:$PATH

WORKDIR /oracleservice
COPY assets ./assets
COPY oracleservice ./

ENTRYPOINT ["python3", "-u", "start.py"]