ROOT    := $(abspath ../../..)
BR_HOST := $(ROOT)/30_TEST/40_Build/buildroot/out-b/host
SYSROOT := $(ROOT)/30_TEST/40_Build/buildroot/out-b/staging

CC      := $(BR_HOST)/bin/arm-linux-gcc
STRIP   := $(BR_HOST)/bin/arm-linux-strip

CFLAGS  := -Os -Wall -Wextra -std=c99 -pipe
LDFLAGS := -lpng16 -lz -lX11

SRC     := src/lcd_capture.c
TARGET  := out/lcd-capture
HOST_BIN := build/lcd-capture-host

.PHONY: all arm host-test clean

all: arm

arm: $(TARGET)

$(TARGET): $(SRC)
	@mkdir -p build out
	$(CC) $(CFLAGS) -o build/lcd-capture.unstripped $(SRC) $(LDFLAGS)
	cp -f build/lcd-capture.unstripped $(TARGET)
	$(STRIP) $(TARGET)

host-test: $(SRC)
	@mkdir -p build
	gcc -O2 -Wall -Wextra -std=c99 -DNO_X11 \
	  $$( [ -f /usr/include/png.h ] || printf ' -DNO_PNG' ) \
	  -o $(HOST_BIN) $(SRC) \
	  $$( [ -f /usr/include/png.h ] && printf ' -lpng16 -lz' )

clean:
	rm -rf build out

