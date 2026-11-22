/*
 * lcd_capture.c — HP Prime G1 (S3C2416) Live LCD / Framebuffer Capture Tool
 *
 * Supports:
 *   --source auto|hw|x11|fb|test
 *     - hw   : Read live simple-framebuffer scanout memory via /dev/mem
 *              (default physical address from /proc/iomem or 0x31F80000, x8r8g8b8)
 *     - x11  : Capture root window via X11 XGetImage
 *     - fb   : Capture /dev/fb0 via Linux fbdev ioctl + mmap/read
 *     - test : Generate 320x240 x8r8g8b8 color-bar test image
 *   --format auto|png|bmp|ppm
 *   -o <path|-> : Output file path or '-' for stdout streaming
 */

#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#ifndef NO_PNG
#include <png.h>
#endif

#ifndef NO_X11
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#endif

#define DEFAULT_PHYS_ADDR  0x31F80000UL
#define DEFAULT_WIDTH      320U
#define DEFAULT_HEIGHT     240U

typedef enum {
    SRC_AUTO = 0,
    SRC_HW,
    SRC_X11,
    SRC_FB,
    SRC_TEST
} CaptureSource;

typedef enum {
    FMT_AUTO = 0,
    FMT_PNG,
    FMT_BMP,
    FMT_PPM
} OutputFormat;

typedef struct {
    CaptureSource source;
    OutputFormat format;
    const char *out_path;
    const char *fb_dev;
    const char *x_display;
    uint32_t width;
    uint32_t height;
    unsigned long phys_addr;
    bool phys_override;
    unsigned int delay_sec;
    bool info_only;
} Options;

static int str_ieq(const char *a, const char *b)
{
    while (*a && *b) {
        int ca = tolower((unsigned char)*a);
        int cb = tolower((unsigned char)*b);
        if (ca != cb) {
            return 0;
        }
        a++;
        b++;
    }
    return *a == '\0' && *b == '\0';
}

static bool ends_with_ci(const char *str, const char *suffix)
{
    size_t slen = strlen(str);
    size_t xlen = strlen(suffix);
    if (slen < xlen) {
        return false;
    }
    return str_ieq(str + slen - xlen, suffix) != 0;
}

static unsigned int extract_bits(unsigned long pixel, unsigned int offset, unsigned int length)
{
    if (length == 0U) {
        return 0U;
    }
    unsigned long mask = (length >= 32U) ? 0xFFFFFFFFUL : ((1UL << length) - 1UL);
    unsigned long val = (pixel >> offset) & mask;
    if (length == 8U) {
        return (unsigned int)val;
    }
    return (unsigned int)((val * 255UL + (mask / 2UL)) / mask);
}

#ifndef NO_X11
static unsigned int extract_mask(unsigned long pixel, unsigned long mask)
{
    if (mask == 0UL) {
        return 0U;
    }
    unsigned int shift = 0U;
    while (((mask >> shift) & 1UL) == 0UL && shift < 32U) {
        shift++;
    }
    unsigned long shifted = mask >> shift;
    unsigned long val = (pixel >> shift) & shifted;
    if (shifted == 255UL) {
        return (unsigned int)val;
    }
    return (unsigned int)((val * 255UL + (shifted / 2UL)) / shifted);
}
#endif

/* Parse start address of "simple-framebuffer" from /proc/iomem */
static bool find_simplefb_phys(unsigned long *out_addr)
{
    FILE *fp = fopen("/proc/iomem", "r");
    if (!fp) {
        return false;
    }
    char line[256];
    bool found = false;
    while (fgets(line, sizeof(line), fp) != NULL) {
        if (strstr(line, "simple-framebuffer") != NULL) {
            char *endp = NULL;
            unsigned long start = strtoul(line, &endp, 16);
            if (endp != line && *endp == '-') {
                *out_addr = start;
                found = true;
                break;
            }
        }
    }
    fclose(fp);
    return found;
}

static void convert_xrgb8888_to_rgb24(const uint32_t *src, uint8_t *dst, uint32_t width, uint32_t height)
{
    size_t total = (size_t)width * (size_t)height;
    for (size_t i = 0; i < total; i++) {
        uint32_t px = src[i];
        dst[i * 3U + 0U] = (uint8_t)((px >> 16) & 0xFFU);
        dst[i * 3U + 1U] = (uint8_t)((px >> 8) & 0xFFU);
        dst[i * 3U + 2U] = (uint8_t)(px & 0xFFU);
    }
}

static int capture_hw(const Options *opt, uint8_t **out_rgb, uint32_t *out_w, uint32_t *out_h)
{
    unsigned long phys = opt->phys_addr;
    if (!opt->phys_override) {
        unsigned long detected = 0UL;
        if (find_simplefb_phys(&detected)) {
            phys = detected;
        }
    }

    long page_sz_l = sysconf(_SC_PAGESIZE);
    if (page_sz_l <= 0) {
        page_sz_l = 4096L;
    }
    size_t page_sz = (size_t)page_sz_l;

    uint32_t w = opt->width;
    uint32_t h = opt->height;
    size_t fb_bytes = (size_t)w * (size_t)h * 4U;

    unsigned long page_base = phys & ~((unsigned long)page_sz - 1UL);
    size_t page_off = (size_t)(phys - page_base);
    size_t map_len = page_off + fb_bytes;

    int fd = open("/dev/mem", O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "lcd-capture[hw]: failed to open /dev/mem: %s\n", strerror(errno));
        return -1;
    }

    void *mapped = mmap(NULL, map_len, PROT_READ, MAP_SHARED, fd, (off_t)page_base);
    if (mapped == MAP_FAILED) {
        fprintf(stderr, "lcd-capture[hw]: mmap(0x%lX, %zu) failed: %s\n",
                phys, map_len, strerror(errno));
        close(fd);
        return -1;
    }

    uint32_t *bounce = malloc(fb_bytes);
    uint8_t *rgb = malloc((size_t)w * (size_t)h * 3U);
    if (!bounce || !rgb) {
        fprintf(stderr, "lcd-capture[hw]: out of memory\n");
        free(bounce);
        free(rgb);
        munmap(mapped, map_len);
        close(fd);
        return -1;
    }

    const volatile uint32_t *vsrc = (const volatile uint32_t *)((const uint8_t *)mapped + page_off);
    size_t total_px = (size_t)w * (size_t)h;
    for (size_t i = 0; i < total_px; i++) {
        bounce[i] = vsrc[i];
    }

    munmap(mapped, map_len);
    close(fd);

    convert_xrgb8888_to_rgb24(bounce, rgb, w, h);
    free(bounce);

    *out_rgb = rgb;
    *out_w = w;
    *out_h = h;
    return 0;
}

#ifndef NO_X11
static int capture_x11(const Options *opt, uint8_t **out_rgb, uint32_t *out_w, uint32_t *out_h)
{
    const char *disp_name = opt->x_display;
    if (!disp_name || disp_name[0] == '\0') {
        disp_name = getenv("DISPLAY");
        if (!disp_name || disp_name[0] == '\0') {
            disp_name = ":0.0";
        }
    }

    Display *dpy = XOpenDisplay(disp_name);
    if (!dpy) {
        fprintf(stderr, "lcd-capture[x11]: cannot open display '%s'\n", disp_name);
        return -1;
    }

    Window root = DefaultRootWindow(dpy);
    XWindowAttributes attrs;
    if (!XGetWindowAttributes(dpy, root, &attrs) || attrs.width <= 0 || attrs.height <= 0) {
        fprintf(stderr, "lcd-capture[x11]: XGetWindowAttributes failed\n");
        XCloseDisplay(dpy);
        return -1;
    }

    uint32_t w = (uint32_t)attrs.width;
    uint32_t h = (uint32_t)attrs.height;
    XImage *img = XGetImage(dpy, root, 0, 0, w, h, AllPlanes, ZPixmap);
    if (!img) {
        fprintf(stderr, "lcd-capture[x11]: XGetImage(%ux%u) failed\n", w, h);
        XCloseDisplay(dpy);
        return -1;
    }

    uint8_t *rgb = malloc((size_t)w * (size_t)h * 3U);
    if (!rgb) {
        fprintf(stderr, "lcd-capture[x11]: out of memory\n");
        XDestroyImage(img);
        XCloseDisplay(dpy);
        return -1;
    }

    unsigned long r_mask = img->red_mask ? img->red_mask : 0x00FF0000UL;
    unsigned long g_mask = img->green_mask ? img->green_mask : 0x0000FF00UL;
    unsigned long b_mask = img->blue_mask ? img->blue_mask : 0x000000FFUL;

    for (uint32_t y = 0; y < h; y++) {
        for (uint32_t x = 0; x < w; x++) {
            unsigned long px = XGetPixel(img, (int)x, (int)y);
            size_t idx = ((size_t)y * (size_t)w + (size_t)x) * 3U;
            rgb[idx + 0U] = (uint8_t)extract_mask(px, r_mask);
            rgb[idx + 1U] = (uint8_t)extract_mask(px, g_mask);
            rgb[idx + 2U] = (uint8_t)extract_mask(px, b_mask);
        }
    }

    XDestroyImage(img);
    XCloseDisplay(dpy);

    *out_rgb = rgb;
    *out_w = w;
    *out_h = h;
    return 0;
}
#endif

static int capture_fb(const Options *opt, uint8_t **out_rgb, uint32_t *out_w, uint32_t *out_h)
{
    int fd = open(opt->fb_dev, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "lcd-capture[fb]: failed to open '%s': %s\n", opt->fb_dev, strerror(errno));
        return -1;
    }

    struct fb_var_screeninfo vinfo;
    struct fb_fix_screeninfo finfo;
    memset(&vinfo, 0, sizeof(vinfo));
    memset(&finfo, 0, sizeof(finfo));

    if (ioctl(fd, FBIOGET_VSCREENINFO, &vinfo) < 0 || ioctl(fd, FBIOGET_FSCREENINFO, &finfo) < 0) {
        fprintf(stderr, "lcd-capture[fb]: ioctl failed on '%s': %s\n", opt->fb_dev, strerror(errno));
        close(fd);
        return -1;
    }

    if (vinfo.xres == 0U || vinfo.yres == 0U ||
        (vinfo.bits_per_pixel != 16U && vinfo.bits_per_pixel != 24U && vinfo.bits_per_pixel != 32U)) {
        fprintf(stderr, "lcd-capture[fb]: unsupported mode %ux%u %ubpp\n",
                vinfo.xres, vinfo.yres, vinfo.bits_per_pixel);
        close(fd);
        return -1;
    }

    uint32_t w = vinfo.xres;
    uint32_t h = vinfo.yres;
    uint32_t bpp = vinfo.bits_per_pixel;
    uint32_t stride = finfo.line_length ? finfo.line_length : (w * (bpp / 8U));
    size_t need_bytes = (size_t)vinfo.yoffset * stride + (size_t)h * stride;
    size_t map_bytes = finfo.smem_len > need_bytes ? (size_t)finfo.smem_len : need_bytes;

    bool used_mmap = false;
    uint8_t *raw = mmap(NULL, map_bytes, PROT_READ, MAP_SHARED, fd, 0);
    if (raw != MAP_FAILED) {
        used_mmap = true;
    } else {
        raw = malloc(need_bytes);
        if (!raw) {
            fprintf(stderr, "lcd-capture[fb]: out of memory\n");
            close(fd);
            return -1;
        }
        size_t got = 0U;
        while (got < need_bytes) {
            ssize_t n = read(fd, raw + got, need_bytes - got);
            if (n < 0) {
                if (errno == EINTR) {
                    continue;
                }
                fprintf(stderr, "lcd-capture[fb]: read failed: %s\n", strerror(errno));
                free(raw);
                close(fd);
                return -1;
            }
            if (n == 0) {
                break;
            }
            got += (size_t)n;
        }
        if (got < need_bytes) {
            fprintf(stderr, "lcd-capture[fb]: short read (%zu < %zu)\n", got, need_bytes);
            free(raw);
            close(fd);
            return -1;
        }
    }

    uint8_t *rgb = malloc((size_t)w * (size_t)h * 3U);
    if (!rgb) {
        fprintf(stderr, "lcd-capture[fb]: out of memory\n");
        if (used_mmap) {
            munmap(raw, map_bytes);
        } else {
            free(raw);
        }
        close(fd);
        return -1;
    }

    unsigned int r_off = vinfo.red.length ? vinfo.red.offset : (bpp == 16U ? 11U : 16U);
    unsigned int r_len = vinfo.red.length ? vinfo.red.length : (bpp == 16U ? 5U : 8U);
    unsigned int g_off = vinfo.green.length ? vinfo.green.offset : (bpp == 16U ? 5U : 8U);
    unsigned int g_len = vinfo.green.length ? vinfo.green.length : (bpp == 16U ? 6U : 8U);
    unsigned int b_off = vinfo.blue.length ? vinfo.blue.offset : 0U;
    unsigned int b_len = vinfo.blue.length ? vinfo.blue.length : (bpp == 16U ? 5U : 8U);

    uint32_t bytes_pp = bpp / 8U;
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t *row = raw + ((size_t)(y + vinfo.yoffset) * stride) + ((size_t)vinfo.xoffset * bytes_pp);
        for (uint32_t x = 0; x < w; x++) {
            const uint8_t *p = row + ((size_t)x * bytes_pp);
            unsigned long px = 0UL;
            if (bpp == 32U) {
                px = (unsigned long)p[0] | ((unsigned long)p[1] << 8) |
                     ((unsigned long)p[2] << 16) | ((unsigned long)p[3] << 24);
            } else if (bpp == 24U) {
                px = (unsigned long)p[0] | ((unsigned long)p[1] << 8) |
                     ((unsigned long)p[2] << 16);
            } else {
                px = (unsigned long)p[0] | ((unsigned long)p[1] << 8);
            }
            size_t idx = ((size_t)y * (size_t)w + (size_t)x) * 3U;
            rgb[idx + 0U] = (uint8_t)extract_bits(px, r_off, r_len);
            rgb[idx + 1U] = (uint8_t)extract_bits(px, g_off, g_len);
            rgb[idx + 2U] = (uint8_t)extract_bits(px, b_off, b_len);
        }
    }

    if (used_mmap) {
        munmap(raw, map_bytes);
    } else {
        free(raw);
    }
    close(fd);

    *out_rgb = rgb;
    *out_w = w;
    *out_h = h;
    return 0;
}

static int capture_test(const Options *opt, uint8_t **out_rgb, uint32_t *out_w, uint32_t *out_h)
{
    uint32_t w = opt->width;
    uint32_t h = opt->height;
    size_t px_count = (size_t)w * (size_t)h;

    uint32_t *xrgb = malloc(px_count * 4U);
    uint8_t *rgb = malloc(px_count * 3U);
    if (!xrgb || !rgb) {
        fprintf(stderr, "lcd-capture[test]: out of memory\n");
        free(xrgb);
        free(rgb);
        return -1;
    }

    uint32_t split_y = (h * 2U) / 3U;
    for (uint32_t y = 0; y < h; y++) {
        for (uint32_t x = 0; x < w; x++) {
            uint32_t px;
            if (y < split_y) {
                uint32_t band = (x * 5U) / w;
                switch (band) {
                case 0: px = 0x00FF0000U; break; /* Pure Red */
                case 1: px = 0x0000FF00U; break; /* Pure Green */
                case 2: px = 0x000000FFU; break; /* Pure Blue */
                case 3: px = 0x00FFFFFFU; break; /* Pure White */
                default: px = 0x00000000U; break; /* Pure Black */
                }
            } else {
                uint32_t g = (w > 1U) ? ((x * 255U) / (w - 1U)) : 0U;
                px = (g << 16) | (g << 8) | g;
            }
            xrgb[(size_t)y * (size_t)w + (size_t)x] = px;
        }
    }

    convert_xrgb8888_to_rgb24(xrgb, rgb, w, h);
    free(xrgb);

    *out_rgb = rgb;
    *out_w = w;
    *out_h = h;
    return 0;
}

#ifndef NO_PNG
static int write_png(FILE *fp, const uint8_t *rgb, uint32_t w, uint32_t h)
{
    png_structp png_ptr = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    if (!png_ptr) {
        fprintf(stderr, "lcd-capture: png_create_write_struct failed\n");
        return -1;
    }

    png_infop info_ptr = png_create_info_struct(png_ptr);
    if (!info_ptr) {
        png_destroy_write_struct(&png_ptr, NULL);
        fprintf(stderr, "lcd-capture: png_create_info_struct failed\n");
        return -1;
    }

    png_bytep *rows = malloc((size_t)h * sizeof(png_bytep));
    if (!rows) {
        png_destroy_write_struct(&png_ptr, &info_ptr);
        fprintf(stderr, "lcd-capture: out of memory allocating PNG rows\n");
        return -1;
    }
    for (uint32_t y = 0; y < h; y++) {
        rows[y] = (png_bytep)(rgb + ((size_t)y * (size_t)w * 3U));
    }

    if (setjmp(png_jmpbuf(png_ptr))) {
        free(rows);
        png_destroy_write_struct(&png_ptr, &info_ptr);
        fprintf(stderr, "lcd-capture: libpng write error\n");
        return -1;
    }

    png_init_io(png_ptr, fp);
    png_set_IHDR(png_ptr, info_ptr, w, h, 8,
                 PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
    png_write_info(png_ptr, info_ptr);
    png_write_image(png_ptr, rows);
    png_write_end(png_ptr, NULL);

    free(rows);
    png_destroy_write_struct(&png_ptr, &info_ptr);
    return 0;
}
#endif

static void put_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFFU);
    p[1] = (uint8_t)((v >> 8) & 0xFFU);
}

static void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFU);
    p[1] = (uint8_t)((v >> 8) & 0xFFU);
    p[2] = (uint8_t)((v >> 16) & 0xFFU);
    p[3] = (uint8_t)((v >> 24) & 0xFFU);
}

static int write_bmp(FILE *fp, const uint8_t *rgb, uint32_t w, uint32_t h)
{
    uint32_t row_bytes = w * 3U;
    uint32_t row_stride = (row_bytes + 3U) & ~3U;
    uint32_t pad = row_stride - row_bytes;
    uint32_t img_bytes = row_stride * h;
    uint32_t file_bytes = 54U + img_bytes;

    uint8_t hdr[54];
    memset(hdr, 0, sizeof(hdr));
    hdr[0] = 'B';
    hdr[1] = 'M';
    put_le32(hdr + 2, file_bytes);
    put_le32(hdr + 10, 54U);
    put_le32(hdr + 14, 40U);
    put_le32(hdr + 18, w);
    put_le32(hdr + 22, h);
    put_le16(hdr + 26, 1U);
    put_le16(hdr + 28, 24U);
    put_le32(hdr + 34, img_bytes);

    if (fwrite(hdr, 1U, sizeof(hdr), fp) != sizeof(hdr)) {
        return -1;
    }

    uint8_t *row_buf = malloc(row_stride);
    if (!row_buf) {
        return -1;
    }

    int rc = 0;
    for (uint32_t y = h; y > 0U; y--) {
        const uint8_t *src = rgb + ((size_t)(y - 1U) * (size_t)w * 3U);
        for (uint32_t x = 0; x < w; x++) {
            row_buf[x * 3U + 0U] = src[x * 3U + 2U]; /* B */
            row_buf[x * 3U + 1U] = src[x * 3U + 1U]; /* G */
            row_buf[x * 3U + 2U] = src[x * 3U + 0U]; /* R */
        }
        for (uint32_t p = 0; p < pad; p++) {
            row_buf[row_bytes + p] = 0U;
        }
        if (fwrite(row_buf, 1U, row_stride, fp) != row_stride) {
            rc = -1;
            break;
        }
    }

    free(row_buf);
    return rc;
}

static int write_ppm(FILE *fp, const uint8_t *rgb, uint32_t w, uint32_t h)
{
    if (fprintf(fp, "P6\n%u %u\n255\n", w, h) < 0) {
        return -1;
    }
    size_t total = (size_t)w * (size_t)h * 3U;
    if (fwrite(rgb, 1U, total, fp) != total) {
        return -1;
    }
    return 0;
}

static void print_info(const Options *opt)
{
    unsigned long phys = DEFAULT_PHYS_ADDR;
    bool found_iomem = find_simplefb_phys(&phys);

    printf("lcd-capture hardware diagnostics:\n");
    printf("  simple-framebuffer (/proc/iomem): %s (phys=0x%08lX)\n",
           found_iomem ? "FOUND" : "NOT FOUND (fallback 0x31F80000)", phys);

    int mem_fd = open("/dev/mem", O_RDONLY);
    if (mem_fd >= 0) {
        printf("  /dev/mem                        : readable (O_RDONLY OK)\n");
        close(mem_fd);
    } else {
        printf("  /dev/mem                        : %s\n", strerror(errno));
    }

    int fb_fd = open(opt->fb_dev, O_RDONLY);
    if (fb_fd >= 0) {
        struct fb_var_screeninfo vinfo;
        struct fb_fix_screeninfo finfo;
        memset(&vinfo, 0, sizeof(vinfo));
        memset(&finfo, 0, sizeof(finfo));
        if (ioctl(fb_fd, FBIOGET_VSCREENINFO, &vinfo) == 0 &&
            ioctl(fb_fd, FBIOGET_FSCREENINFO, &finfo) == 0) {
            printf("  %s                       : id='%s' %ux%u %ubpp stride=%u smem=%u\n",
                   opt->fb_dev, finfo.id, vinfo.xres, vinfo.yres,
                   vinfo.bits_per_pixel, finfo.line_length, finfo.smem_len);
        } else {
            printf("  %s                       : ioctl failed (%s)\n", opt->fb_dev, strerror(errno));
        }
        close(fb_fd);
    } else {
        printf("  %s                       : %s\n", opt->fb_dev, strerror(errno));
    }

#ifndef NO_X11
    const char *disp_name = opt->x_display;
    if (!disp_name || disp_name[0] == '\0') {
        disp_name = getenv("DISPLAY");
        if (!disp_name || disp_name[0] == '\0') {
            disp_name = ":0.0";
        }
    }
    Display *dpy = XOpenDisplay(disp_name);
    if (dpy) {
        Window root = DefaultRootWindow(dpy);
        XWindowAttributes attrs;
        if (XGetWindowAttributes(dpy, root, &attrs)) {
            printf("  X11 (%s)                      : connected %dx%d depth=%d\n",
                   disp_name, attrs.width, attrs.height, attrs.depth);
        } else {
            printf("  X11 (%s)                      : connected (XGetWindowAttributes failed)\n",
                   disp_name);
        }
        XCloseDisplay(dpy);
    } else {
        printf("  X11 (%s)                      : unreachable\n", disp_name);
    }
#else
    printf("  X11                             : disabled at build time\n");
#endif

#ifndef NO_PNG
    printf("  Output formats                  : png, bmp, ppm\n");
#else
    printf("  Output formats                  : bmp, ppm (png disabled)\n");
#endif
}

static void print_usage(const char *prog)
{
    printf(
        "Usage: %s [OPTIONS]\n"
        "Capture HP Prime G1 live LCD framebuffer (320x240) to PNG, BMP, or PPM.\n\n"
        "Options:\n"
        "  -o, --output <path|->     Output file path, or '-' for stdout\n"
        "                            (default: ./lcd_YYYYMMDD_HHMMSS.<ext>)\n"
        "  -f, --format <fmt>        auto | png | bmp | ppm (default: auto)\n"
        "  -s, --source <src>        auto | hw | x11 | fb | test (default: auto)\n"
        "                            auto tries hw -> x11 -> fb\n"
        "  -d, --delay <sec>         Wait N seconds before capturing (default: 0)\n"
        "      --phys <hex>          Override physical framebuffer address (hw)\n"
        "      --width <px>          Width for hw/test source (default: 320)\n"
        "      --height <px>         Height for hw/test source (default: 240)\n"
        "      --fbdev <path>        Framebuffer device for fb source (default: /dev/fb0)\n"
        "      --display <disp>      X11 display name for x11 source (default: $DISPLAY or :0.0)\n"
        "      --info                Print framebuffer & X11 diagnostics and exit\n"
        "  -h, --help                Show this help\n",
        prog
    );
}

static const char *default_ext(OutputFormat fmt)
{
    switch (fmt) {
    case FMT_PNG: return "png";
    case FMT_BMP: return "bmp";
    case FMT_PPM: return "ppm";
    default:
#ifndef NO_PNG
        return "png";
#else
        return "bmp";
#endif
    }
}

int main(int argc, char *argv[])
{
    Options opt;
    memset(&opt, 0, sizeof(opt));
    opt.source = SRC_AUTO;
    opt.format = FMT_AUTO;
    opt.fb_dev = "/dev/fb0";
    opt.width = DEFAULT_WIDTH;
    opt.height = DEFAULT_HEIGHT;
    opt.phys_addr = DEFAULT_PHYS_ADDR;

    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        if (strcmp(arg, "-h") == 0 || strcmp(arg, "--help") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(arg, "--info") == 0) {
            opt.info_only = true;
        } else if ((strcmp(arg, "-o") == 0 || strcmp(arg, "--output") == 0) && i + 1 < argc) {
            opt.out_path = argv[++i];
        } else if ((strcmp(arg, "-f") == 0 || strcmp(arg, "--format") == 0) && i + 1 < argc) {
            const char *v = argv[++i];
            if (str_ieq(v, "auto")) {
                opt.format = FMT_AUTO;
            } else if (str_ieq(v, "png")) {
                opt.format = FMT_PNG;
            } else if (str_ieq(v, "bmp")) {
                opt.format = FMT_BMP;
            } else if (str_ieq(v, "ppm")) {
                opt.format = FMT_PPM;
            } else {
                fprintf(stderr, "lcd-capture: unknown format '%s'\n", v);
                return 2;
            }
        } else if ((strcmp(arg, "-s") == 0 || strcmp(arg, "--source") == 0) && i + 1 < argc) {
            const char *v = argv[++i];
            if (str_ieq(v, "auto")) {
                opt.source = SRC_AUTO;
            } else if (str_ieq(v, "hw")) {
                opt.source = SRC_HW;
            } else if (str_ieq(v, "x11")) {
                opt.source = SRC_X11;
            } else if (str_ieq(v, "fb")) {
                opt.source = SRC_FB;
            } else if (str_ieq(v, "test")) {
                opt.source = SRC_TEST;
            } else {
                fprintf(stderr, "lcd-capture: unknown source '%s'\n", v);
                return 2;
            }
        } else if ((strcmp(arg, "-d") == 0 || strcmp(arg, "--delay") == 0) && i + 1 < argc) {
            long d = strtol(argv[++i], NULL, 10);
            if (d < 0 || d > 3600) {
                fprintf(stderr, "lcd-capture: invalid delay\n");
                return 2;
            }
            opt.delay_sec = (unsigned int)d;
        } else if (strcmp(arg, "--phys") == 0 && i + 1 < argc) {
            opt.phys_addr = strtoul(argv[++i], NULL, 0);
            opt.phys_override = true;
        } else if (strcmp(arg, "--width") == 0 && i + 1 < argc) {
            opt.width = (uint32_t)strtoul(argv[++i], NULL, 10);
        } else if (strcmp(arg, "--height") == 0 && i + 1 < argc) {
            opt.height = (uint32_t)strtoul(argv[++i], NULL, 10);
        } else if (strcmp(arg, "--fbdev") == 0 && i + 1 < argc) {
            opt.fb_dev = argv[++i];
        } else if (strcmp(arg, "--display") == 0 && i + 1 < argc) {
            opt.x_display = argv[++i];
        } else if (arg[0] != '-' && !opt.out_path) {
            opt.out_path = arg;
        } else {
            fprintf(stderr, "lcd-capture: invalid or incomplete argument '%s'\n", arg);
            print_usage(argv[0]);
            return 2;
        }
    }

    if (opt.width == 0U || opt.height == 0U || opt.width > 4096U || opt.height > 4096U) {
        fprintf(stderr, "lcd-capture: invalid dimensions %ux%u\n", opt.width, opt.height);
        return 2;
    }

    if (opt.info_only) {
        print_info(&opt);
        return 0;
    }

    if (opt.format == FMT_AUTO) {
        if (opt.out_path && strcmp(opt.out_path, "-") != 0) {
            if (ends_with_ci(opt.out_path, ".bmp")) {
                opt.format = FMT_BMP;
            } else if (ends_with_ci(opt.out_path, ".ppm")) {
                opt.format = FMT_PPM;
            } else if (ends_with_ci(opt.out_path, ".png")) {
                opt.format = FMT_PNG;
            }
        }
        if (opt.format == FMT_AUTO) {
#ifndef NO_PNG
            opt.format = FMT_PNG;
#else
            opt.format = FMT_BMP;
#endif
        }
    }

#ifdef NO_PNG
    if (opt.format == FMT_PNG) {
        fprintf(stderr, "lcd-capture: PNG support was disabled at build time\n");
        return 1;
    }
#endif

    if (opt.delay_sec > 0U) {
        struct timespec ts;
        ts.tv_sec = (time_t)opt.delay_sec;
        ts.tv_nsec = 0L;
        nanosleep(&ts, NULL);
    }

    uint8_t *rgb = NULL;
    uint32_t cap_w = 0U;
    uint32_t cap_h = 0U;
    const char *used_src = NULL;

    if (opt.source == SRC_HW) {
        if (capture_hw(&opt, &rgb, &cap_w, &cap_h) != 0) {
            return 1;
        }
        used_src = "hw";
    } else if (opt.source == SRC_X11) {
#ifndef NO_X11
        if (capture_x11(&opt, &rgb, &cap_w, &cap_h) != 0) {
            return 1;
        }
        used_src = "x11";
#else
        fprintf(stderr, "lcd-capture: X11 support was disabled at build time\n");
        return 1;
#endif
    } else if (opt.source == SRC_FB) {
        if (capture_fb(&opt, &rgb, &cap_w, &cap_h) != 0) {
            return 1;
        }
        used_src = "fb";
    } else if (opt.source == SRC_TEST) {
        if (capture_test(&opt, &rgb, &cap_w, &cap_h) != 0) {
            return 1;
        }
        used_src = "test";
    } else {
        if (capture_hw(&opt, &rgb, &cap_w, &cap_h) == 0) {
            used_src = "hw";
#ifndef NO_X11
        } else if (capture_x11(&opt, &rgb, &cap_w, &cap_h) == 0) {
            used_src = "x11";
#endif
        } else if (capture_fb(&opt, &rgb, &cap_w, &cap_h) == 0) {
            used_src = "fb";
        } else {
            fprintf(stderr, "lcd-capture: all auto sources (hw, x11, fb) failed\n");
            return 1;
        }
    }

    char auto_name[64];
    const char *dest_name = opt.out_path;
    bool to_stdout = false;
    if (!dest_name) {
        time_t now = time(NULL);
        struct tm tm_now;
        if (localtime_r(&now, &tm_now) != NULL) {
            snprintf(auto_name, sizeof(auto_name),
                     "lcd_%04d%02d%02d_%02d%02d%02d.%s",
                     tm_now.tm_year + 1900, tm_now.tm_mon + 1, tm_now.tm_mday,
                     tm_now.tm_hour, tm_now.tm_min, tm_now.tm_sec,
                     default_ext(opt.format));
        } else {
            snprintf(auto_name, sizeof(auto_name), "lcd_capture.%s", default_ext(opt.format));
        }
        dest_name = auto_name;
    } else if (strcmp(dest_name, "-") == 0) {
        to_stdout = true;
    }

    FILE *fp = to_stdout ? stdout : fopen(dest_name, "wb");
    if (!fp) {
        fprintf(stderr, "lcd-capture: failed to open output '%s': %s\n", dest_name, strerror(errno));
        free(rgb);
        return 1;
    }

    int write_rc = -1;
    if (opt.format == FMT_PNG) {
#ifndef NO_PNG
        write_rc = write_png(fp, rgb, cap_w, cap_h);
#endif
    } else if (opt.format == FMT_BMP) {
        write_rc = write_bmp(fp, rgb, cap_w, cap_h);
    } else {
        write_rc = write_ppm(fp, rgb, cap_w, cap_h);
    }

    if (to_stdout) {
        fflush(fp);
    } else {
        if (fclose(fp) != 0) {
            write_rc = -1;
        }
    }
    free(rgb);

    if (write_rc != 0) {
        fprintf(stderr, "lcd-capture: failed to write image\n");
        return 1;
    }

    if (!to_stdout) {
        fprintf(stderr, "lcd-capture: saved %ux%u (%s -> %s) to %s\n",
                cap_w, cap_h, used_src, default_ext(opt.format), dest_name);
    }
    return 0;
}
