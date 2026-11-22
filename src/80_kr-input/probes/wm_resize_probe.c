/*
 * wm_resize_probe.c — Gate 2B Probe: WM-less XTerm window discovery and
 * dynamic XMoveResizeWindow (320x240 <-> 320x96 / 320x112) verification.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>

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

int main(int argc, char *argv[])
{
    int target_h = (argc > 1) ? atoi(argv[1]) : 96;
    if (target_h < 40 || target_h > 240)
        target_h = 240;

    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) {
        fprintf(stderr, "wm_resize_probe: cannot open DISPLAY\n");
        return 1;
    }

    Window xterm_win = find_xterm_window(dpy, DefaultRootWindow(dpy));
    if (xterm_win == None) {
        fprintf(stderr, "wm_resize_probe: XTerm window not found\n");
        XCloseDisplay(dpy);
        return 1;
    }

    XMoveResizeWindow(dpy, xterm_win, 0, 0, 320, (unsigned int)target_h);
    XSync(dpy, False);
    printf("Resized XTerm window (0x%lx) to 320x%d\n", (unsigned long)xterm_win, target_h);
    XCloseDisplay(dpy);
    return 0;
}

