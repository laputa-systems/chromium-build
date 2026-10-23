# Gate F graph oracle

Gate F audits the generated Ninja commands for the `chrome` target. It asks
Ninja to enumerate those commands twice and requires identical output. A
second classifier audits the second list and compares its command counts with
Gate F's report. The check runs in the pinned build image without network
access.

This is an independent check of command selection and classification. It does
not build Chromium or establish that the commands succeed. After a completed
Chrome build, repeat the command and response-file audit against the generated
graph.

## Generated evidence

Gate F writes these files beneath the work volume's `/work/metadata`:

- `chrome-commands.txt` and `chrome-oracle-commands.txt`: the two Ninja lists.
- `chrome-dry-run.txt`: `ninja -n chrome` output.
- `gate-f-report.json` and `gate-f-oracle-report.json`: audit results and
  independently classified command counts.

These files are generated evidence, not checked-in baselines. Review both
reports and the command lists when a graph changes. The primary and oracle
reports must complete with no failures or mismatches. `ninja -t compdb` is not
a substitute: it describes the generated graph rather than the `chrome`
target's command closure.

## Regeneration

For a changed source, patch, profile, or GN argument, prepare and regenerate the
GN graph before running Gate F:

```sh
./chromium-build prepare --arch amd64
./chromium-build gate-c --arch amd64
./chromium-build gate-d --arch amd64
./chromium-build gate-f --arch amd64
```

If preparation is unchanged and only GN output needs refreshing, run Gate D
and Gate F. Gate F does not build `chrome`.
