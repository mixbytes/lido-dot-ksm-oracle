FROM python:3.8-slim

# Build

RUN apt-get update \
 && apt-get install -y gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --trusted-host pypi.python.org -r requirements.txt
RUN apt-get purge -y --auto-remove gcc
WORKDIR /oracleservice


# Set metadata
ARG PARA_ID
ARG INITIAL_BLOCK_NUMBER
ARG ERA_DURATION

ENV PARA_ID=${PARA_ID:-999}
ENV ABI_PATH=./assets/oracle.json
ENV INITIAL_BLOCK_NUMBER=${INITIAL_BLOCK_NUMBER:-1}
ENV ERA_DURATION=${ERA_DURATION:-30}


COPY assets ./assets
COPY oracleservice ./

ENTRYPOINT ["python3", "-u", "start.py"]
