/*
 * hpp_vkbd.c — Ultra-Lightweight X11 Touchscreen Virtual Keyboard for HP Prime G1
 *
 * Features:
 *   - Pure Xlib + XTest + X11 Core Bitmap Fonts (kr-half / kr-full PCF), 0 Xft overhead
 *   - 320x240 layout management: automatically resizes XTerm to 320x96 on launch
 *     and restores XTerm to 320x240 on exit (SIGTERM/SIGINT or [X] close button)
 *   - 16px Composition & Status bar (y: 96..111) + 4x10 Key Grid (y: 112..239, 32x32px keys)
 *   - Native 2-Bulsik Korean Hangul Automata with pre-commit composition buffer
 *     (eliminates BusyBox ash multibyte backspace corruption)
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <X11/extensions/XTest.h>

#include "hangul_automata.h"
#include "keymap_layout.h"

#define SCREEN_W      320
#define SCREEN_H      240
#define XTERM_OPEN_H  96
#define VKBD_Y        96
#define VKBD_W        320
#define VKBD_H        144
#define BAR_H         16
#define KEY_W         32
#define KEY_H         32

static volatile sig_atomic_t g_running = 1;

static void sig_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

static Window find_xterm_window(Display *dpy, Window win)
{
    XClassHint hint;
    if (XGetClassHint(dpy, win, &hint)) {
        int match = (hint.res_class && !strcmp(hint.res_class, "XTerm")) ||
                    (hint.res_name && !strcmp(hint.res_name, "xterm"));
        if (hint.res_name)
            XFree(hint.res_name);
        if (hint.res_class)
            XFree(hint.res_class);
        if (match)
            return win;
    }

    Window root_ret, parent_ret, *children = NULL;
    unsigned int nchildren = 0;
    if (!XQueryTree(dpy, win, &root_ret, &parent_ret, &children, &nchildren))
        return None;

    Window found = None;
    for (unsigned int i = 0; i < nchildren; i++) {
        found = find_xterm_window(dpy, children[i]);
        if (found != None)
            break;
    }
    if (children)
        XFree(children);
    return found;
}

static void resize_xterm(Display *dpy, int height)
{
    Window xw = find_xterm_window(dpy, DefaultRootWindow(dpy));
    if (xw != None) {
        XMoveResizeWindow(dpy, xw, 0, 0, SCREEN_W, (unsigned int)height);
        XSync(dpy, False);
    }
}

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

typedef struct {
    Display *dpy;
    int screen;
    Window win;
    GC gc;
    XFontStruct *fn_half;
    XFontStruct *fn_full;
    KeyCode scratch_kc[2];
    int scratch_slot;

    bool is_korean;
    bool is_sym;
    bool shift_oneshot;
    bool ctrl_oneshot;
    hangul_state_t hangul;

    int pressed_row;
    int pressed_idx;

    unsigned long col_bg;
    unsigned long col_key;
    unsigned long col_key_spec;
    unsigned long col_key_act;
    unsigned long col_border;
    unsigned long col_text;
    unsigned long col_bar_bg;
    unsigned long col_preview;
} vkbd_ctx_t;

static unsigned long alloc_rgb(Display *dpy, int scr, uint8_t r, uint8_t g, uint8_t b)
{
    Colormap cmap = DefaultColormap(dpy, scr);
    XColor xc;
    xc.red   = (unsigned short)r << 8;
    xc.green = (unsigned short)g << 8;
    xc.blue  = (unsigned short)b << 8;
    xc.flags = DoRed | DoGreen | DoBlue;
    if (XAllocColor(dpy, cmap, &xc))
        return xc.pixel;
    return WhitePixel(dpy, scr);
}

static layer_id_t current_layer(const vkbd_ctx_t *ctx)
{
    if (ctx->is_sym)
        return ctx->shift_oneshot ? LAYER_SYM_2 : LAYER_SYM_1;
    if (ctx->is_korean)
        return ctx->shift_oneshot ? LAYER_KR_SHIFT : LAYER_KR_BASE;
    return ctx->shift_oneshot ? LAYER_EN_UPPER : LAYER_EN_LOWER;
}

static void inject_keysym(vkbd_ctx_t *ctx, KeySym ks, bool with_ctrl)
{
    KeyCode ctrl_kc = 0;
    if (with_ctrl) {
        ctrl_kc = XKeysymToKeycode(ctx->dpy, XK_Control_L);
        if (ctrl_kc)
            XTestFakeKeyEvent(ctx->dpy, ctrl_kc, True, CurrentTime);
    }

    KeyCode kc = XKeysymToKeycode(ctx->dpy, ks);
    if (kc != 0 && ks < 0x01000000U) {
        /* Check if unshifted or shifted in existing keymap; for simplicity use scratch slot for ASCII symbols to avoid Shift state mismatch */
        if ((ks >= 0xFF00U)) {
            XTestFakeKeyEvent(ctx->dpy, kc, True, CurrentTime);
            XTestFakeKeyEvent(ctx->dpy, kc, False, CurrentTime);
            if (with_ctrl && ctrl_kc)
                XTestFakeKeyEvent(ctx->dpy, ctrl_kc, False, CurrentTime);
            XSync(ctx->dpy, False);
            return;
        }
    }

    /* Map dynamically to ping-pong scratch keycode slot */
    KeyCode slot_kc = ctx->scratch_kc[ctx->scratch_slot];
    ctx->scratch_slot ^= 1;
    XChangeKeyboardMapping(ctx->dpy, slot_kc, 1, &ks, 1);
    XSync(ctx->dpy, False);
    usleep(8000);
    XTestFakeKeyEvent(ctx->dpy, slot_kc, True, CurrentTime);
    XTestFakeKeyEvent(ctx->dpy, slot_kc, False, CurrentTime);

    if (with_ctrl && ctrl_kc)
        XTestFakeKeyEvent(ctx->dpy, ctrl_kc, False, CurrentTime);
    XSync(ctx->dpy, False);
}

static void inject_codepoint(vkbd_ctx_t *ctx, uint32_t cp)
{
    bool with_ctrl = ctx->ctrl_oneshot;
    ctx->ctrl_oneshot = false;

    if (with_ctrl && ((cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z'))) {
        KeySym ks = (cp >= 'a' && cp <= 'z') ? (XK_a + (cp - 'a')) : (XK_a + (cp - 'A'));
        KeyCode ctrl_kc = XKeysymToKeycode(ctx->dpy, XK_Control_L);
        KeyCode char_kc = XKeysymToKeycode(ctx->dpy, ks);
        if (ctrl_kc && char_kc) {
            XTestFakeKeyEvent(ctx->dpy, ctrl_kc, True, CurrentTime);
            XTestFakeKeyEvent(ctx->dpy, char_kc, True, CurrentTime);
            XTestFakeKeyEvent(ctx->dpy, char_kc, False, CurrentTime);
            XTestFakeKeyEvent(ctx->dpy, ctrl_kc, False, CurrentTime);
            XSync(ctx->dpy, False);
            return;
        }
    }

    KeySym ks = (cp < 0x100U) ? (KeySym)cp : (KeySym)(0x01000000U | cp);
    inject_keysym(ctx, ks, with_ctrl);
}

static void flush_hangul(vkbd_ctx_t *ctx)
{
    uint32_t cp = 0;
    if (hangul_flush(&ctx->hangul, &cp))
        inject_codepoint(ctx, cp);
}

static void draw_uchar16(vkbd_ctx_t *ctx, int x, int y, uint16_t u16)
{
    if (!ctx->fn_full)
        return;
    XChar2b c2b;
    c2b.byte1 = (unsigned char)((u16 >> 8) & 0xFFU);
    c2b.byte2 = (unsigned char)(u16 & 0xFFU);
    XSetFont(ctx->dpy, ctx->gc, ctx->fn_full->fid);
    XDrawString16(ctx->dpy, ctx->win, ctx->gc, x, y, &c2b, 1);
}

static void draw_ascii(vkbd_ctx_t *ctx, int x, int y, const char *s)
{
    if (!ctx->fn_half || !s)
        return;
    XSetFont(ctx->dpy, ctx->gc, ctx->fn_half->fid);
    XDrawString(ctx->dpy, ctx->win, ctx->gc, x, y, s, (int)strlen(s));
}

static void draw_vkbd(vkbd_ctx_t *ctx)
{
    /* 1. Draw 16px Status & Composition Bar (y: 0..15) */
    XSetForeground(ctx->dpy, ctx->gc, ctx->col_bar_bg);
    XFillRectangle(ctx->dpy, ctx->win, ctx->gc, 0, 0, VKBD_W, BAR_H);

    XSetForeground(ctx->dpy, ctx->gc, ctx->col_border);
    XDrawLine(ctx->dpy, ctx->win, ctx->gc, 0, 0, VKBD_W - 1, 0);
    XDrawLine(ctx->dpy, ctx->win, ctx->gc, 0, BAR_H - 1, VKBD_W - 1, BAR_H - 1);

    const char *mode_str = ctx->is_sym ? (ctx->shift_oneshot ? "[SYM2]" : "[123]") :
                           (ctx->is_korean ? (ctx->shift_oneshot ? "[KR+S]" : "[HAN]") :
                                             (ctx->shift_oneshot ? "[EN+S]" : "[EN]"));
    XSetForeground(ctx->dpy, ctx->gc, ctx->col_text);
    draw_ascii(ctx, 4, 12, mode_str);

    if (ctx->ctrl_oneshot) {
        XSetForeground(ctx->dpy, ctx->gc, ctx->col_preview);
        draw_ascii(ctx, 46, 12, "CTRL");
    }

    /* Center: Hangul composition preview */
    uint32_t comp = hangul_current_codepoint(&ctx->hangul);
    XSetForeground(ctx->dpy, ctx->gc, ctx->col_preview);
    draw_ascii(ctx, 92, 12, "Comp:[");
    if (comp != 0) {
        draw_uchar16(ctx, 130, 13, (uint16_t)comp);
    }
    draw_ascii(ctx, 144, 12, "]");

    /* Right side buttons on bar: [Commit] (x: 208..268), [Close X] (x: 272..318) */
    XSetForeground(ctx->dpy, ctx->gc, ctx->col_key_spec);
    XFillRectangle(ctx->dpy, ctx->win, ctx->gc, 208, 1, 60, BAR_H - 2);
    XFillRectangle(ctx->dpy, ctx->win, ctx->gc, 272, 1, 46, BAR_H - 2);
    XSetForeground(ctx->dpy, ctx->gc, ctx->col_border);
    XDrawRectangle(ctx->dpy, ctx->win, ctx->gc, 208, 1, 59, BAR_H - 3);
    XDrawRectangle(ctx->dpy, ctx->win, ctx->gc, 272, 1, 45, BAR_H - 3);

    XSetForeground(ctx->dpy, ctx->gc, ctx->col_text);
    draw_ascii(ctx, 220, 12, "OK");
    draw_uchar16(ctx, 234, 13, 0x2192);
    draw_ascii(ctx, 278, 12, "HIDE X");

    /* 2. Draw 4x10 Key Grid (y: 16..143) */
    layer_id_t lid = current_layer(ctx);
    int half_w = ctx->fn_half ? ctx->fn_half->max_bounds.width : 6;
    int full_w = ctx->fn_full ? ctx->fn_full->max_bounds.width : 12;

    for (int r = 0; r < NUM_KEY_ROWS; r++) {
        for (int i = 0; i < MAX_KEYS_PER_ROW; i++) {
            const vkey_t *k = &KEYBOARD_LAYERS[lid][r][i];
            if (k->span == 0 || k->act == ACT_NONE)
                continue;

            int kx = k->col * KEY_W;
            int ky = BAR_H + r * KEY_H;
            int kw = k->span * KEY_W;
            int kh = KEY_H;

            bool is_active = (ctx->pressed_row == r && ctx->pressed_idx == i) ||
                             (k->act == ACT_SHIFT && ctx->shift_oneshot) ||
                             (k->act == ACT_CTRL && ctx->ctrl_oneshot);
            bool is_special = (k->act != ACT_CHAR && k->act != ACT_JAMO);

            unsigned long bg = is_active ? ctx->col_key_act :
                               (is_special ? ctx->col_key_spec : ctx->col_key);

            XSetForeground(ctx->dpy, ctx->gc, bg);
            XFillRectangle(ctx->dpy, ctx->win, ctx->gc, kx, ky, kw, kh);

            XSetForeground(ctx->dpy, ctx->gc, ctx->col_border);
            XDrawRectangle(ctx->dpy, ctx->win, ctx->gc, kx, ky, kw - 1, kh - 1);

            XSetForeground(ctx->dpy, ctx->gc, ctx->col_text);
            int ty = ky + 21;
            if (k->ulabel != 0) {
                int tx = kx + (kw - full_w) / 2;
                draw_uchar16(ctx, tx, ty, k->ulabel);
            } else if (k->label != NULL) {
                int len = (int)strlen(k->label);
                int tx = kx + (kw - len * half_w) / 2;
                draw_ascii(ctx, tx, ty, k->label);
            }
        }
    }
    XFlush(ctx->dpy);
}

static void handle_bar_click(vkbd_ctx_t *ctx, int x)
{
    if (x >= 272) {
        /* [HIDE X] clicked -> clean exit & restore XTerm 320x240 */
        flush_hangul(ctx);
        g_running = 0;
    } else if (x >= 90 && x < 270) {
        /* Composition preview or [OK->] clicked -> commit current syllable */
        flush_hangul(ctx);
        draw_vkbd(ctx);
    }
}

static void handle_key_press(vkbd_ctx_t *ctx, int r, int idx)
{
    layer_id_t lid = current_layer(ctx);
    const vkey_t *k = &KEYBOARD_LAYERS[lid][r][idx];

    switch (k->act) {
    case ACT_SHIFT:
        ctx->shift_oneshot = !ctx->shift_oneshot;
        break;

    case ACT_LANG:
        flush_hangul(ctx);
        ctx->is_sym = false;
        ctx->is_korean = !ctx->is_korean;
        ctx->shift_oneshot = false;
        break;

    case ACT_SYM:
        flush_hangul(ctx);
        ctx->is_sym = !ctx->is_sym;
        ctx->shift_oneshot = false;
        break;

    case ACT_CTRL:
        flush_hangul(ctx);
        ctx->ctrl_oneshot = !ctx->ctrl_oneshot;
        break;

    case ACT_JAMO: {
        uint32_t committed[2] = {0, 0};
        int n = hangul_feed_jamo(&ctx->hangul, k->code, committed);
        for (int i = 0; i < n; i++)
            inject_codepoint(ctx, committed[i]);
        ctx->shift_oneshot = false;
        break;
    }

    case ACT_CHAR:
        flush_hangul(ctx);
        inject_codepoint(ctx, k->code);
        ctx->shift_oneshot = false;
        break;

    case ACT_KEYSYM:
        if (k->code == 0xFF08) { /* BackSpace */
            if (hangul_backspace(&ctx->hangul)) {
                /* Consumed inside Hangul composition buffer! */
                break;
            }
        } else {
            flush_hangul(ctx);
        }
        inject_keysym(ctx, (KeySym)k->code, ctx->ctrl_oneshot);
        ctx->ctrl_oneshot = false;
        ctx->shift_oneshot = false;
        break;

    default:
        break;
    }
}

int main(void)
{
    signal(SIGTERM, sig_handler);
    signal(SIGINT, sig_handler);
    signal(SIGHUP, sig_handler);

    vkbd_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    hangul_init(&ctx.hangul);
    ctx.pressed_row = -1;
    ctx.pressed_idx = -1;
    ctx.is_korean = true; /* Start in Korean 2-Bulsik by default */

    ctx.dpy = XOpenDisplay(NULL);
    if (!ctx.dpy) {
        fprintf(stderr, "hpp-vkbd: cannot open DISPLAY\n");
        return 1;
    }
    ctx.screen = DefaultScreen(ctx.dpy);

    if (find_free_keycodes(ctx.dpy, ctx.scratch_kc) < 0) {
        fprintf(stderr, "hpp-vkbd: no free keycodes available\n");
        XCloseDisplay(ctx.dpy);
        return 1;
    }

    /* Load Korean PCF core bitmap fonts (fallback to fixed if alias missing) */
    ctx.fn_half = XLoadQueryFont(ctx.dpy, "kr-half");
    if (!ctx.fn_half)
        ctx.fn_half = XLoadQueryFont(ctx.dpy, "fixed");
    ctx.fn_full = XLoadQueryFont(ctx.dpy, "kr-full");
    if (!ctx.fn_full)
        ctx.fn_full = ctx.fn_half;

    ctx.col_bg       = alloc_rgb(ctx.dpy, ctx.screen, 24,  28,  36);
    ctx.col_key      = alloc_rgb(ctx.dpy, ctx.screen, 45,  52,  64);
    ctx.col_key_spec = alloc_rgb(ctx.dpy, ctx.screen, 32,  38,  48);
    ctx.col_key_act  = alloc_rgb(ctx.dpy, ctx.screen, 59, 130, 246);
    ctx.col_border   = alloc_rgb(ctx.dpy, ctx.screen, 75,  85,  99);
    ctx.col_text     = alloc_rgb(ctx.dpy, ctx.screen, 243, 244, 246);
    ctx.col_bar_bg   = alloc_rgb(ctx.dpy, ctx.screen, 17,  24,  39);
    ctx.col_preview  = alloc_rgb(ctx.dpy, ctx.screen, 250, 204, 21);

    /* Create override_redirect dock window so XTerm never loses input focus */
    XSetWindowAttributes swa;
    memset(&swa, 0, sizeof(swa));
    swa.override_redirect = True;
    swa.background_pixel  = ctx.col_bg;
    swa.border_pixel      = ctx.col_border;
    swa.event_mask        = ExposureMask | ButtonPressMask | ButtonReleaseMask;

    ctx.win = XCreateWindow(
        ctx.dpy, DefaultRootWindow(ctx.dpy),
        0, VKBD_Y, VKBD_W, VKBD_H, 0,
        CopyFromParent, InputOutput, CopyFromParent,
        CWOverrideRedirect | CWBackPixel | CWBorderPixel | CWEventMask,
        &swa
    );

    ctx.gc = XCreateGC(ctx.dpy, ctx.win, 0, NULL);

    /* Shrink XTerm to top 320x96 and map virtual keyboard at bottom 320x144 */
    resize_xterm(ctx.dpy, XTERM_OPEN_H);
    XMapRaised(ctx.dpy, ctx.win);
    draw_vkbd(&ctx);

    int xfd = ConnectionNumber(ctx.dpy);
    while (g_running) {
        while (XPending(ctx.dpy) > 0) {
            XEvent ev;
            XNextEvent(ctx.dpy, &ev);
            if (ev.type == Expose) {
                if (ev.xexpose.count == 0)
                    draw_vkbd(&ctx);
            } else if (ev.type == ButtonPress) {
                int mx = ev.xbutton.x;
                int my = ev.xbutton.y;
                if (my < BAR_H) {
                    handle_bar_click(&ctx, mx);
                } else {
                    int r = (my - BAR_H) / KEY_H;
                    int c = mx / KEY_W;
                    if (r >= 0 && r < NUM_KEY_ROWS && c >= 0 && c < 10) {
                        layer_id_t lid = current_layer(&ctx);
                        for (int i = 0; i < MAX_KEYS_PER_ROW; i++) {
                            const vkey_t *k = &KEYBOARD_LAYERS[lid][r][i];
                            if (k->span > 0 && c >= k->col && c < (k->col + k->span)) {
                                ctx.pressed_row = r;
                                ctx.pressed_idx = i;
                                draw_vkbd(&ctx);
                                handle_key_press(&ctx, r, i);
                                break;
                            }
                        }
                    }
                }
            } else if (ev.type == ButtonRelease) {
                if (ctx.pressed_row >= 0) {
                    ctx.pressed_row = -1;
                    ctx.pressed_idx = -1;
                    draw_vkbd(&ctx);
                }
            }
        }

        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(xfd, &rfds);
        struct timeval tv = { .tv_sec = 0, .tv_usec = 100000 };
        select(xfd + 1, &rfds, NULL, NULL, &tv);
    }

    /* Clean shutdown: flush pending Hangul, restore scratch keycodes, restore XTerm 320x240 */
    flush_hangul(&ctx);
    KeySym nosym = NoSymbol;
    XChangeKeyboardMapping(ctx.dpy, ctx.scratch_kc[0], 1, &nosym, 1);
    XChangeKeyboardMapping(ctx.dpy, ctx.scratch_kc[1], 1, &nosym, 1);
    XUnmapWindow(ctx.dpy, ctx.win);
    resize_xterm(ctx.dpy, SCREEN_H);
    XDestroyWindow(ctx.dpy, ctx.win);
    XSync(ctx.dpy, False);
    XCloseDisplay(ctx.dpy);
    return 0;
}

