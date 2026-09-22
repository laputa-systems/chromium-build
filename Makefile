ARCH ?= arm64
PLATFORM ?= linux
ifeq ($(PLATFORM),macos)
PROFILE ?= macos-release
else
PROFILE ?= headless-debug
endif
IMAGE ?= ungoogled-chromium-builder:150.0.7871.114-$(ARCH)
WORK_VOLUME ?= ungoogled-chromium-work-$(ARCH)
CACHE_VOLUME ?= ungoogled-chromium-ccache-$(ARCH)

CHROMIUM_BUILD = CHROMIUM_BUILDER_IMAGE=$(IMAGE) \
    CHROMIUM_WORK_VOLUME=$(WORK_VOLUME) \
    CHROMIUM_CACHE_VOLUME=$(CACHE_VOLUME) \
    ./chromium-build --platform $(PLATFORM) --arch $(ARCH) --profile $(PROFILE)
DOCKER ?= docker

.NOTPARALLEL:
.PHONY: image fetch prepare gate-a gate-b gate-c gate-d gate-e gate-f gates
.PHONY: bootstrap full-build resume-build build-target stage-runtime test-fast test-functional test-staged cache-stats resume-check graph-breakdown check-py
.PHONY: macos-doctor macos-smoke-preflight macos-update-lock macos-fetch macos-prepare macos-configure macos-cache-stats macos-build macos-stage-runtime macos-test-staged

check-py:
	PYTHONPATH=scripts:tests uv run --with ruff --with ty ruff check scripts tests
	PYTHONPATH=scripts:tests uv run --with ruff --with ty ty check scripts tests

image:
	$(CHROMIUM_BUILD) image

fetch:
	$(CHROMIUM_BUILD) fetch

prepare:
	$(CHROMIUM_BUILD) prepare

gate-a:
	$(CHROMIUM_BUILD) gate-a

gate-b:
	$(CHROMIUM_BUILD) gate-b

gate-c:
	$(CHROMIUM_BUILD) gate-c

gate-d:
	$(CHROMIUM_BUILD) gate-d

gate-e:
	$(CHROMIUM_BUILD) gate-e

gate-f:
	$(CHROMIUM_BUILD) gate-f

bootstrap: image fetch prepare

gates: gate-a gate-b gate-c gate-d gate-e gate-f

full-build: bootstrap gates
	$(CHROMIUM_BUILD) build

resume-build: gate-e gate-f
	$(CHROMIUM_BUILD) build

build-target:
	@test -n "$(TARGET)" || (echo 'usage: make build-target TARGET=obj/path.o' >&2; exit 2)
	$(CHROMIUM_BUILD) build-target "$(TARGET)"

stage-runtime:
	$(CHROMIUM_BUILD) stage-runtime

test-fast:
	$(CHROMIUM_BUILD) test-fast

test-functional:
	$(CHROMIUM_BUILD) test

test-staged:
	$(CHROMIUM_BUILD) test-staged

cache-stats:
	$(DOCKER) run --rm --network=none -v $(CACHE_VOLUME):/ccache $(IMAGE) \
		sh -c 'CCACHE_DIR=/ccache ccache -s'

resume-check:
	$(DOCKER) run --rm --network=none -v $(WORK_VOLUME):/work $(IMAGE) \
		sh -c 'cd /work/out/$(PROFILE) && ninja -n chrome 2>/dev/null | tail -1'

graph-breakdown:
	$(DOCKER) run --rm --network=none -v "$(CURDIR):/repo:ro" -v $(WORK_VOLUME):/work $(IMAGE) \
		python3 /repo/scripts/build-task-breakdown.py \
		--out /work/out/$(PROFILE) --target chrome

macos-doctor:
	./chromium-build --platform macos --arch arm64 --profile macos-release doctor

macos-smoke-preflight:
	MACOS_COMPILER_CACHE=off ./chromium-build --platform macos --arch arm64 --profile macos-release smoke-preflight

macos-update-lock:
	./chromium-build --platform macos --arch arm64 --profile macos-release update-lock --latest

macos-fetch:
	./chromium-build --platform macos --arch arm64 --profile macos-release fetch

macos-prepare:
	./chromium-build --platform macos --arch arm64 --profile macos-release prepare

macos-configure:
	./chromium-build --platform macos --arch arm64 --profile macos-release configure

macos-cache-stats:
	./chromium-build --platform macos --arch arm64 --profile macos-release cache-stats

macos-build:
	./chromium-build --platform macos --arch arm64 --profile macos-release build

macos-stage-runtime:
	./chromium-build --platform macos --arch arm64 --profile macos-release stage-runtime

macos-test-staged:
	./chromium-build --platform macos --arch arm64 --profile macos-release test-staged
