# CHB-11 Linux candidate result

The 1 ms back-to-back begin-frame patch in `1a71f38` suppresses bctl's
browser UI frame-message flood. The candidate remains unpromoted because
the Linux comparison does not establish an overall news latency win.
CHB-11 remains open.

The amd64 build completed `prepare`, `poc`, `build`, `test-fast`, sandbox
compilation, `stage-runtime`, `test-staged` and `package` on 2026-11-16.
The candidate archive SHA-256 is
`5dddea248c9683bdda2cb592f7f79010b64dcad93676c73af2a41228752e65eb`.
Its r6 filename is for local testing and must change before publication.
The ordinary-user extracted-package sandbox load check remains unrun.

The [bctl experiment record](https://github.com/joshuarli/bctl/tree/f8405bb8/tests/bench/results/2026-11-16-bounded-begin-frames)
contains the script, build phase exits, acceptance report, image digests,
raw receipts, screenshots and traces. Both images use the same bctl release
binary and kernel, with two vCPUs and production browser flags on Linux/KVM.

Separate five-cycle traces reduce browser-main `CompositorFrameSinkClient`
messages from 5,137 to 165 and `DidNotProduceFrame` events from 5,127 to 66.
Five alternating ten-cycle timing pairs pass all 100 measured cycles and
20 host-load checks. Every final screenshot is byte-identical.

The table gives the median of five session medians and their range.
CPU uses five session deltas over ten cycles each.

| Metric | Published r6 baseline | Candidate |
| --- | --- | --- |
| JavaScript click to World DOM, ms | 28.25 (27.20–28.90) | 23.35 (22.45–25.95) |
| Screenshot response, ms | 34.42 (33.31–35.14) | 37.24 (36.20–40.97) |
| Sum of navigation, click and screenshot, ms | 104.85 (104.66–112.10) | 102.42 (96.91–115.57) |
| Live session process CPU per ten cycles, s | 3.33 (3.14–3.55) | 2.65 (2.60–2.97) |

CPU excludes the generic bctl daemon, probe client and exited processes.
The operation sum excludes sleeps, startup and agent command overhead.
The candidate's click and CPU distributions are lower, but screenshots are
slower and the combined operation distributions overlap.

The comparison uses a published archive rather than a rebuilt patch-only
baseline. Go, Node, libxslt and compiler configuration digests also changed
between the earlier checkout and this candidate. Host load declined during
the batch despite passing every load gate. These confounders prevent assigning
all timing and CPU differences to the patch.

The Linux result supports the mechanism experiment. It does not establish
the macOS/HVF benefit or the remaining latency limiter. The recorded arm64
image attempt failed on unavailable Alpine pins (CHB-10), and the owner chose
amd64 verification first. An isolated baseline, arm64 build, macOS workflow
comparison, screenshot regression check and wider browser qualification
still block adoption. No browser release or bctl production pin changed.
