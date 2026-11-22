/*
 * xtest_utf8_probe.c — Gate 2A Probe: Dynamic X11 Keysym (0x01000000 | Unicode)
 * injection via XChangeKeyboardMapping + XTestFakeKeyEvent on HP Prime G1.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <X11/extensions/XTest.h>

static int find_free_keycodes(Display *dpy, KeyCode out_kc[2])
{
    int min_kc = 0, max_kc = 0, ks_per_kc = 0;
    XDisplayKeycodes(dpy, &min_kc, &max_kc);
    KeySym *map = XGetKeyboardMapping(dpy, min_kc, max_kc - min_kc + 1, &ks_per_kc);
    if (!map)
        return -1;

    int found = 0;
    for (int kc = max_kc; kc >= min_kc && found < 2; kc--) {
        KeySym *row = &map[(kc - min_kc) * ks_per_kc];
        int empty = 1;
        for (int j = 0; j < ks_per_kc; j++) {
            if (row[j] != NoSymbol) {
                empty = 0;
                break;
            }
        }
        if (empty)
            out_kc[found++] = (KeyCode)kc;
    }
    XFree(map);
    return (found == 2) ? 0 : -1;
}

static void send_unicode_char(Display *dpy, KeyCode kc, uint32_t codepoint, int delay_ms)
{
    KeySym ks = (codepoint < 0x100) ? (KeySym)codepoint : (KeySym)(0x01000000U | codepoint);
    XChangeKeyboardMapping(dpy, kc, 1, &ks, 1);
    XSync(dpy, False);
    if (delay_ms > 0)
        usleep(delay_ms * 1000);
    XTestFakeKeyEvent(dpy, kc, True, CurrentTime);
    XTestFakeKeyEvent(dpy, kc, False, CurrentTime);
    XSync(dpy, False);
}

int main(int argc, char *argv[])
{
    int delay_ms = (argc > 1) ? atoi(argv[1]) : 10;
    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) {
        fprintf(stderr, "xtest_utf8_probe: cannot open DISPLAY\n");
        return 1;
    }

    KeyCode kc[2] = {0, 0};
    if (find_free_keycodes(dpy, kc) < 0) {
        fprintf(stderr, "xtest_utf8_probe: no free keycodes found\n");
        XCloseDisplay(dpy);
        return 1;
    }
    printf("Using ping-pong free keycodes: %u, %u (delay=%dms)\n", kc[0], kc[1], delay_ms);

    /* Test sequence: "한글 값 닭 읽어\n" */
    const uint32_t seq[] = {
        0xD55C, 0xAE00, 0x0020, 0xAC12, 0x0020, 0xB2ED, 0x0020, 0xC77D, 0xC5B4
    };
    int slot = 0;
    for (size_t i = 0; i < sizeof(seq) / sizeof(seq[0]); i++) {
        send_unicode_char(dpy, kc[slot], seq[i], delay_ms);
        slot ^= 1;
    }

    KeyCode enter_kc = XKeysymToKeycode(dpy, XK_Return);
    if (enter_kc) {
        XTestFakeKeyEvent(dpy, enter_kc, True, CurrentTime);
        XTestFakeKeyEvent(dpy, enter_kc, False, CurrentTime);
        XSync(dpy, False);
    }

    /* Restore temporary keycodes to NoSymbol */
    KeySym nosym = NoSymbol;
    XChangeKeyboardMapping(dpy, kc[0], 1, &nosym, 1);
    XChangeKeyboardMapping(dpy, kc[1], 1, &nosym, 1);
    XSync(dpy, False);
    XCloseDisplay(dpy);
    return 0;
}

