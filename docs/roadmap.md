# Roadmap

This is the single plan for this repository: all current and future work, and the handoff for resuming it. Campaigns are sections below, and their items interleave in **Sequence**. "Resume the roadmap" means continue from **Status**.

## Status

- **State:** no campaign is active. The owner dropped the Fil-C Hydrogen feasibility campaign on 2026-11-24.
- **Next:** the tickets under Later, in priority order.

## Later

- CHB-11 · P1 · bound back-to-back begin frames sent to the browser UI compositor in unthrottled headless
- CHB-21 · P1 · Replace the Helium build with ungoogled Chromium for Wayland on arm64 and amd64
- CHB-8 · P2 · complete the Helium behavior acceptance matrix
- CHB-9 · P2 · forward-port Helium's native adblock-rust experiment
- CHB-16 · P2 · make HarfRust Hydrogen's shaper, then remove HarfBuzz
- CHB-18 · P3 · qualify browser payload reductions against required appliance capabilities
- CHB-20 · P3 · native adblock refreshes its filter catalog from Helium's services at startup
- CHB-23 · P3 · Share the helpers the build scripts copy (was DSLP-6)
- CHB-24 · P3 · Stop macos_backend.main rebinding module globals (was DSLP-7)
- CHB-25 · P3 · Kill the whole process group when stream_command times out (was DSLP-8)
- CHB-26 · P3 · Replace dict[str, Any] build documents with typed models (was DSLP-9)
- CHB-27 · P3 · Test the macOS backend entry point and its exit codes (was DSLP-10)
- CHB-28 · P3 · Measure and test the Linux image scripts on a Linux host (was DSLP-11)
- CHB-29 · P3 · Add a mutation lane for the release-gating scripts (was DSLP-12)
- CHB-22 · P4 · Hydrogen's allocator is unguarded and alternatives are unmeasured; keep PartitionAlloc unless a workload shows a large win
- CHB-31 · P4 · Share the Helium and Chromium adblock setup scripts or retire one (was DSLP-14)
- CHB-32 · P4 · Lint the shell entrypoints (was DSLP-15)
- CHB-33 · P4 · Read the Rust components list from the backend in the policy check (was DSLP-16)
- CHB-34 · P4 · Needs owner · reconcile the no-linters rule with make lint (was DSLP-17)

