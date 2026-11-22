# Makefile for Lode Runner NG — HP Prime G1 Linux (SDL 1.2 port)
CROSS ?= arm-linux-
CC = $(CROSS)gcc
SYSROOT ?= $(HOME)/buildroot/output/host/arm-buildroot-linux-gnueabi/sysroot

CFLAGS ?= -std=gnu99 -O2 -Wall -Wextra \
          -march=armv5te -msoft-float \
          -D_GNU_SOURCE \
          --sysroot=$(SYSROOT) \
          -I. \
          -I$(SYSROOT)/usr/include \
          -I$(SYSROOT)/usr/include/SDL

LDFLAGS ?= --sysroot=$(SYSROOT) \
           -L$(SYSROOT)/usr/lib \
           -lSDL -lSDL_image -lSDL_mixer -lpthread -lm

SRCS = ai.c \
       animation.c \
       exit.c \
       game.c \
       gold.c \
       guard.c \
       keyhole.c \
       level.c \
       main.c \
       path.c \
       runner.c \
       sound.c \
       texture.c \
       xmalloc.c

OBJS = $(SRCS:.c=.o)
TARGET = loderunner-ng

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) -o $@ $(OBJS) $(LDFLAGS)

%.o: %.c config.h sdl2_compat.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: all clean

