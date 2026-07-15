# Gate F graph oracle

Gate F uses two related checks to establish that the generated `chrome` graph is the graph we intend to audit:

1. Ninja 1.12.1 enumerates the target-scoped command list with `ninja -t commands chrome`.
2. Gate F audits that list for the compiler, linker, archive, libc++, FFmpeg, ccache, and forbidden-path requirements.
3. Ninja enumerates the same target a second time. The two command lists must be byte-for-byte identical.
4. `audit-chrome-oracle.py` classifies the second list independently and compares its counts with `gate-f-report.json`.

The image builds Ninja 1.12.1 from the locked upstream source archive and runs the entire check with `--network=none`. The oracle is therefore independent of the primary Python audit logic, while still using the same generated Ninja graph and the same approved build image.

This is a graph oracle, not a second Chromium build. It verifies command selection and the classification of the selected commands. It does not prove that every command succeeds, that the final executable links, or that the graph remains valid after a complete build. The complete command and response-file audit must be repeated after the first real Chrome build.

## Generated artifacts

Gate F writes these files to the architecture's work volume under `/work/metadata`:

- `chrome-commands.txt`: Ninja's first target-scoped command enumeration.
- `chrome-oracle-commands.txt`: the second enumeration used by the oracle.
- `chrome-dry-run.txt`: output from `ninja -n chrome`.
- `gate-f-report.json`: primary command and toolchain audit.
- `gate-f-oracle-report.json`: independent counts, command-list SHA-256, and comparison result.

The command lists are generated evidence, not checked-in baselines. A graph change may legitimately change their contents or counts. Review the reports and command-list hash together before accepting such a change.

The current arm64 graph snapshot is:

| Command class | Count |
| --- | ---: |
| C/C++ compile | 44,829 |
| Link | 29 |
| Archive | 2,084 |
| Assembly | 240 |

These numbers are observations for the locked source, profile, and GN arguments; they are not hard-coded pass criteria.

## Repeatable regeneration

Set the architecture explicitly. The default work volume and image names are shown here:

```sh
ARCH=arm64
IMAGE=ungoogled-chromium-builder:150.0.7871.114-$ARCH
WORK_VOLUME=ungoogled-chromium-work-$ARCH
```

If Dockerfile, image scripts, package locks, or the locked Ninja/toolchain inputs changed, rebuild the image first:

```sh
./chromium-build image --arch "$ARCH"
```

For a source, patch, profile, or GN-argument change, regenerate the prepared source and GN output as applicable, then regenerate both Gate F reports:

```sh
./chromium-build prepare --arch "$ARCH"
./chromium-build gate-c --arch "$ARCH"
./chromium-build gate-d --arch "$ARCH"
./chromium-build gate-f --arch "$ARCH"
```

If preparation is unchanged and only the generated graph needs to be refreshed, the shorter repeatable sequence is:

```sh
./chromium-build gate-d --arch "$ARCH"
./chromium-build gate-f --arch "$ARCH"
```

`gate-f` overwrites the command lists and both reports. It does not build `chrome`.

## Verification

Inspect both reports from the work volume:

```sh
docker run --rm --network=none \
  -v "$WORK_VOLUME:/work" \
  "$IMAGE" \
  sh -c 'cat /work/metadata/gate-f-report.json; cat /work/metadata/gate-f-oracle-report.json'
```

The primary report and oracle report should both have `"status": "complete"`, an empty `"failures"` array, and matching values for `compile_commands`, `link_commands`, `archive_commands`, `assembly_commands`, and `ranlib_commands`. The oracle report's `mismatches` object must be empty.

Check the Ninja dry-run separately. `ninja -n chrome` may report only a regeneration edge when GN files are stale, or `ninja: no work to do` when the output is current. Neither output is a substitute for the target-scoped `-t commands chrome` enumeration; the latter is what supplies the complete command counts.

For a graph change, review the command-list hash and inspect changed command families before accepting the new reports. At minimum, verify that the primary audit still reports:

- absolute `/opt/llvm-musl/bin/clang` and `clang++` compiler paths wrapped by `/usr/bin/ccache` for C/C++ compilation;
- direct Laputa LLVM archive and ranlib tools;
- LLD and the external static libc++ archives on compiler link commands;
- no in-tree libc++, bundled FFmpeg, GCC/sysroot/libstdc++, or forbidden runtime selections;
- the system FFmpeg shim;
- no non-loopback network route inside the audit container.

After the first successful real Chrome build, rerun the command and response-file audit against the completed graph. Do not promote a changed oracle report solely because its counts look plausible.

## Troubleshooting

If the two Ninja command lists differ, preserve both files and inspect the generation state before rerunning. A changing graph, concurrent GN/Ninja process, or mismatched work volume can cause this failure.

If the oracle counts differ from Gate F, treat that as an audit defect until explained. The independent classifier deliberately ignores Rust commands whose only compiler reference is `-Clinker=/opt/llvm-musl/bin/clang*`; those are Rust linker settings, not direct C/C++ compiler or Chromium link commands.

Do not use `ninja -t compdb` as a count replacement for this check. In Ninja 1.12.1 it describes the whole generated graph rather than the `chrome` target closure, so its count is expected to be different.
