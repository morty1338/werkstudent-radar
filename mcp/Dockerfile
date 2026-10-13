# Remote MCP server (Streamable HTTP) as a container, for any container host (CI checks it;
# the hosted server runs on Vercel, see mcp/DEPLOY.md, and Render could run this image for free).
# Build from the repository root, so the image can include radar/stats.py:
#   docker build -f mcp/Dockerfile -t radar-mcp .
#   docker run -p 8080:8080 radar-mcp        # then http://localhost:8080/health
# Only the files below go into the image (mcp/Dockerfile.dockerignore); the data
# comes from the published site at runtime, so no data or secrets are baked in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    MCP_ALLOWED_HOSTS=* \
    FORWARDED_ALLOW_IPS=*
# MCP_ALLOWED_HOSTS=* and FORWARDED_ALLOW_IPS=*: only the host's proxy can reach the
# container, and it sets the Host and X-Forwarded-For headers.

WORKDIR /app
COPY mcp/requirements.txt mcp/requirements.txt
RUN pip install -r mcp/requirements.txt
COPY radar/__init__.py radar/stats.py radar/
COPY mcp/radar_data.py mcp/radar_tools.py mcp/radar_http.py mcp/radar_server.py mcp/

RUN useradd --create-home --uid 10001 radar
USER radar
EXPOSE 8080
CMD ["python", "mcp/radar_server.py", "--http"]
