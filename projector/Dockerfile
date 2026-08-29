# The projector transcodes and publishes, so like the gate it is only ever as
# correct as the ffmpeg build it ships. Same pin, same reasoning, same three
# values to change together when moving to a newer one: see gate/Dockerfile for
# the full explanation of why this is not the distribution's ffmpeg.
#
# These builds carry VAAPI, but they load libva at run time rather than linking
# it, so the runtime and a driver have to be in the image as well as the render
# node being passed in. Without them ffmpeg aborts on a libva assertion the
# moment it tries to open the device, which looks like a title that starts and
# immediately stops.
FROM debian:trixie-slim AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_RELEASE=autobuild-2026-08-18-15-03
ARG FFMPEG_BUILD=n8.1.2-44-g7c533d0f86
ARG FFMPEG_SHA256_AMD64=03ccc8a1cb534b97c2bc43f322ddb1b7c23bd325abb7e4c31aa37f4b4c0e648f
ARG FFMPEG_SHA256_ARM64=2ffec225398cc41047cb3cf2503d2b1f00f8589648fe572fc94dd20d050ed068
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/* \
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
    && ffmpeg -version | head -1

FROM python:3.12-slim

WORKDIR /app

# Static builds, so they carry their own codecs and pull in no shared libraries.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/

# One font, for the demo cards and for burning in subtitles. Named explicitly by
# the player rather than left to fontconfig, which this base has nothing in.
#
# Plus the VAAPI runtime and drivers, so PROJECTOR_VAAPI_DEVICE actually works:
# libva itself, and a driver for the two common cases (iHD for modern Intel,
# mesa for AMD and older Intel). Leave the variable unset and none of this is
# touched; the encode runs on the CPU.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       fonts-dejavu-core libva2 libva-drm2 intel-media-va-driver mesa-va-drivers \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

CMD ["python", "main.py"]
