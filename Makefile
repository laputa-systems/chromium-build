ARCH ?= arm64
PROFILE ?= headless-debug
IMAGE ?= ungoogled-chromium-builder:150.0.7871.114-$(ARCH)
WORK_VOLUME ?= ungoogled-chromium-work-$(ARCH)
CACHE_VOLUME ?= ungoogled-chromium-ccache-$(ARCH)

CHROMIUM_BUILD = CHROMIUM_BUILDER_IMAGE=$(IMAGE) \
    CHROMIUM_WORK_VOLUME=$(WORK_VOLUME) \
    CHROMIUM_CACHE_VOLUME=$(CACHE_VOLUME) \
    ./chromium-build --arch $(ARCH) --profile $(PROFILE)
DOCKER ?= docker

.NOTPARALLEL:
.PHONY: image fetch prepare gate-a gate-b gate-c gate-d gate-e gate-f gates
.PHONY: bootstrap full-build resume-build build-target cache-stats resume-check

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

cache-stats:
	$(DOCKER) run --rm --network=none -v $(CACHE_VOLUME):/ccache $(IMAGE) \
		sh -c 'CCACHE_DIR=/ccache ccache -s'

resume-check:
	$(DOCKER) run --rm --network=none -v $(WORK_VOLUME):/work $(IMAGE) \
		sh -c 'cd /work/out/$(PROFILE) && ninja -n chrome 2>/dev/null | tail -1'
