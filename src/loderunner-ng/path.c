// Copyright 2024-2025 Viacheslav Chimishuk <vchimishuk@yandex.ru>
// Modified for HP Prime G1 Linux port
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "xmalloc.h"

/*
 * Join two FS paths.
 * If file does not exist at absolute path (e.g. /usr/share/...),
 * attempts to find it at relative path (./share/loderunner-ng/...).
 */
char *path_join(char *a, char *b)
{
    size_t n = strlen(a) + 1 + strlen(b) + 32;
    char *s = xmalloc(n);

    snprintf(s, n, "%s/%s", a, b);

    // If file does not exist, check local fallback
    if (access(s, F_OK) != 0) {
        // Find subfolder name (levels, textures, sounds)
        char *sub = strrchr(a, '/');
        if (sub) {
            sub++;
            char local_path[256];
            snprintf(local_path, sizeof(local_path), "share/loderunner-ng/%s/%s", sub, b);
            if (access(local_path, F_OK) == 0) {
                snprintf(s, n, "%s", local_path);
                return s;
            }
            snprintf(local_path, sizeof(local_path), "./%s/%s", sub, b);
            if (access(local_path, F_OK) == 0) {
                snprintf(s, n, "%s", local_path);
                return s;
            }
        }
    }

    return s;
}
