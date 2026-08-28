# projector

Plays titles from your own media library into an upperroom channel.

It runs on whatever machine holds the library, connects **out** to your gate over
one WebSocket, and publishes to the same RTMP ingest OBS would. Nothing listens
here: no open port, no certificate, no name in DNS. The gate asks it to search,
fetch a poster, play and stop, and it says what it is doing as it goes.

Quick start:

```
cp .env.example .env      # fill in the gate URL, both keys, and your library
docker build -t upperroom-projector .
docker run --rm --env-file .env upperroom-projector
```

Or without Docker: Python 3.12, `pip install -r requirements.txt`, an `ffmpeg`
on the path, then `python main.py`.

Demo mode (`PROJECTOR_DEMO=1`) plays three built-in generated titles and needs no
library at all, which is how the demo stack and local testing work.

The full explanation, the environment table, the security model and the host
guide are in [`docs/11-theater.md`](../docs/11-theater.md).
