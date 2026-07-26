FROM python:3.13-slim
WORKDIR /work
# install agentdeck as a package dependency (wheel built from this repo)
COPY pyproject.toml README.md ./
COPY agentdeck/ agentdeck/
RUN pip install --no-cache-dir ".[serve]"
# the project dir is mounted at runtime: ./.agentdeck -> /work/.agentdeck
EXPOSE 8000
CMD ["agentdeck-serve"]
