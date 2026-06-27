"""
Download the free DB-IP country database at image build time.

This is run once by the Dockerfile, not at runtime. It fetches the current
month's DB-IP IP-to-Country Lite database, which is free to redistribute under
CC-BY 4.0 (data by DB-IP, https://db-ip.com). The gate uses it to enforce the
country allow list. If the download fails, the build still succeeds and the
country lock simply stays inactive until a database is present.
"""

import datetime
import gzip
import urllib.request

OUT = "/app/dbip-country.mmdb"


def url_for(day):
    return f"https://download.db-ip.com/free/dbip-country-lite-{day:%Y-%m}.mmdb.gz"


def main():
    today = datetime.date.today()
    previous = today.replace(day=1) - datetime.timedelta(days=1)
    # Try this month first, then last month in case the new file is not out yet.
    for day in (today, previous):
        try:
            # DB-IP rejects the default urllib user-agent, so set a normal one.
            request = urllib.request.Request(
                url_for(day), headers={"User-Agent": "Mozilla/5.0 (selfstream)"}
            )
            raw = urllib.request.urlopen(request, timeout=30).read()
            with open(OUT, "wb") as out:
                out.write(gzip.decompress(raw))
            print(f"geo database saved for {day:%Y-%m}")
            return
        except Exception as error:
            print(f"geo database fetch failed for {day:%Y-%m}: {error}")
    print("WARNING: no geo database available, country lock will be inactive")


if __name__ == "__main__":
    main()
