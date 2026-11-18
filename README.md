# fm-mate-thefirmwarehub-mirror
Mirror for the JSON feed and media assets provided by thefirmwarehub.com

Run `python3 mirror.py` from the directory where the mirror should be stored.
The script creates `mirror/`, fetches the catalog with today's date as a cache
buster, and saves the catalog and assets under their original paths. If a card
is missing or returns HTTP 404, it uses `img/w0/<id>.webp` instead.

[The Firmware Hub's catalog rules](https://thefirmwarehub.com/kb/#catalog).

On subsequent runs, only new firmware or firmware with a changed SHA-256 is
downloaded. Packages are verified before replacing existing files. Errors,
including checksum mismatches, are logged to standard error and cause a nonzero
exit status. The previous catalog is retained if any firmware fails, so the
failed downloads can be retried on the next run.

Firmware list: The Firmware Hub – https://thefirmwarehub.com. The list is
licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Card texts and pictures belong to their makers and are not covered by that
license; credit each maker and link to their repository when using them.
