# The media store remuxes each finished broadcast and reads its duration, so it
# needs the same ffmpeg and ffprobe the gate records with. This is the same
# pinned static build as gate/Dockerfile, for the same reasons (see the comment
# there): pinned by tag and by checksum, and the four FFMPEG_* values below must
# move together with the gate's, never on their own.
FROM debian:trixie-slim AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_RELEASE=autobuild-2026-09-29-13-10
ARG FFMPEG_BUILD=n8.1.3-6-gff48edd8b2
ARG FFMPEG_SHA256_AMD64=9f96ca3806df5926dc6645a93fab35c2ed3783c9824c319ca99e9dc6dc286875
ARG FFMPEG_SHA256_ARM64=39b7b4d4e5842645d9ccec23f2bbd99d2658c85dba78839ad2025f69906c3546
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    # TARGETARCH is only filled in by BuildKit, and the classic builder leaves it
    # empty, so fall back to asking the base image what it is.
    && arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$arch" in \
         amd64) slug=linux64;    sha="$FFMPEG_SHA256_AMD64" ;; \
         arm64) slug=linuxarm64; sha="$FFMPEG_SHA256_ARM64" ;; \
         *) echo "no pinned ffmpeg build for architecture '$arch'" >&2; exit 1 ;; \
       esac \
    && name="ffmpeg-${FFMPEG_BUILD}-${slug}-gpl-8.1" \
    && curl -fsSL -o /tmp/ffmpeg.tar.xz \
       "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_RELEASE}/${name}.tar.xz" \
    && echo "${sha}  /tmp/ffmpeg.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp "${name}/bin/ffmpeg" "${name}/bin/ffprobe" \
    && mv "/tmp/${name}/bin/ffmpeg" "/tmp/${name}/bin/ffprobe" /usr/local/bin/ \
    && rm -rf /tmp/ffmpeg.tar.xz "/tmp/${name}" \
    # Fail the build here rather than at the first broadcast if the binary does
    # not run on this base at all.
    && ffmpeg -version | head -1


FROM python:3.12-slim

# The root filesystem is mounted read-only in compose, so nothing may try to
# write bytecode next to the code.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Static builds, so they carry their own codecs and pull in no shared libraries.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

# A fixed unprivileged uid, and the media directory created owned by it. A new
# docker volume mounted at /media takes the ownership of the directory it
# covers, so a fresh install is writable by this user and by nobody else. A
# volume made before the store existed is owned by root and needs one chown
# (docs/04-run.md, "Updating").
RUN groupadd --gid 10001 store \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin store \
    && mkdir -p /media \
    && chown 10001:10001 /media
USER 10001:10001

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
