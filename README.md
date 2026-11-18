# fm-mate-thefirmwarehub-mirror
Mirror for the JSON feed and media assets provided by thefirmwarehub.com

Run `python3 mirror.py` from the directory where the mirror should be stored.
The script creates `mirror/`, fetches the catalog with today's date as a cache
buster, and saves the catalog and assets under their original paths. If a card
is missing or returns HTTP 404, it uses `img/w0/<id>.webp` instead.

On subsequent runs, only new firmware or firmware with a changed SHA-256 is
downloaded. Packages are verified before replacing existing files. Errors,
including checksum mismatches, are logged to standard error and cause a nonzero
exit status. The previous catalog is retained if any firmware fails, so the
failed downloads can be retried on the next run.
