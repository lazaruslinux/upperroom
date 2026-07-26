# ffmpeg records the broadcast, cuts clips out of the recording and grabs the
# home page preview frame, so the whole media side of the app is only ever as
# correct as the build we ship.
#
# It is pinned to a specific upstream static build rather than installed from
# the distribution, because Debian trixie ships ffmpeg 7.1.5 and that version
# loses audio on an RTSP read: it demuxes the AAC packets and then muxes none of
# them, producing a recording that has a video track, a declared audio track,
# and no sound. Nothing about the command line changes it. Debian has ffmpeg 8
# only in unstable, and mixing a suite into a stable base to chase one package
# is worse than pinning one binary.
#
# Pinned by tag AND by checksum: the tag is an immutable autobuild, and the
# checksum means a build fails loudly rather than silently picking up something
# else. To move to a newer ffmpeg, change the three values below together.
FROM debian:trixie-slim AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_RELEASE=autobuild-2026-07-24-13-32
ARG FFMPEG_BUILD=n8.1.2-31-g8c9502e9b0
ARG FFMPEG_SHA256_AMD64=67b4011d82c67586c3703c81d3c967a837319f75e5e61551f22a91588e440660
ARG FFMPEG_SHA256_ARM64=9674faaeeafd519add630da412e84a1dabaed3759f516b5da41001a1d345d956
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

WORKDIR /app

# Static builds, so they carry their own codecs and pull in no shared libraries.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY routes ./routes

# Bake the country database into the image (used for the US-only lock).
RUN python fetch_geodb.py || true

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
