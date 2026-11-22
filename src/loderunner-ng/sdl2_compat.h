/*
 * sdl2_compat.h — Lightweight SDL2 to SDL 1.2 compatibility layer for HP Prime G1
 * Enables loderunner-ng to run natively on SDL 1.2 / 320x240 TFT LCD.
 */
#ifndef SDL2_COMPAT_H
#define SDL2_COMPAT_H

#include <SDL/SDL.h>
#include <SDL/SDL_image.h>
#include <SDL/SDL_mixer.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Window / Renderer / Texture opaque wrappers */
typedef struct SDL_Window {
    SDL_Surface *real_screen;
} SDL_Window;

typedef struct SDL_Renderer {
    SDL_Surface *virt_screen;
    SDL_Surface *real_screen;
    Uint8 draw_r, draw_g, draw_b, draw_a;
    Uint32 draw_color;
} SDL_Renderer;

typedef SDL_Surface SDL_Texture;

/* Enums and Constants */
#define SDL_WINDOWPOS_UNDEFINED 0
#define SDL_WINDOW_FULLSCREEN SDL_FULLSCREEN
#define SDL_RENDERER_ACCELERATED 0
#define SDL_HINT_RENDER_SCALE_QUALITY "0"

/* Helper Hints */
static inline int SDL_SetHint(const char *name, const char *val) {
    (void)name; (void)val;
    return 1;
}

static inline int SDL_RenderSetScale(SDL_Renderer *r, float sx, float sy) {
    (void)r; (void)sx; (void)sy;
    return 0;
}

/* 64-bit tick counter */
static inline uint64_t SDL_GetTicks64(void) {
    return (uint64_t)SDL_GetTicks();
}

/* Window creation: sets real LCD mode (320x240) */
static inline SDL_Window *SDL_CreateWindow(const char *title, int x, int y, int w, int h, Uint32 flags) {
    (void)x; (void)y; (void)w; (void)h; (void)flags;
    SDL_WM_SetCaption(title, title);

    // Hide mouse cursor on calculator LCD
    SDL_ShowCursor(SDL_DISABLE);

    // HP Prime screen is 320x240.
    SDL_Surface *real = SDL_SetVideoMode(320, 240, 16, SDL_SWSURFACE);
    if (!real) {
        real = SDL_SetVideoMode(320, 240, 0, SDL_SWSURFACE);
    }
    if (!real) {
        fprintf(stderr, "error: SDL_SetVideoMode failed: %s\n", SDL_GetError());
        return NULL;
    }

    fprintf(stderr, "info: Video mode set: %dx%d @ %d bpp (R:0x%x G:0x%x B:0x%x A:0x%x)\n",
            real->w, real->h, real->format->BitsPerPixel,
            real->format->Rmask, real->format->Gmask, real->format->Bmask, real->format->Amask);

    // Clear screen to black immediately to wipe out any residual blue framebuffer
    SDL_FillRect(real, NULL, SDL_MapRGB(real->format, 0, 0, 0));
    SDL_Flip(real);

    SDL_Window *win = (SDL_Window *)malloc(sizeof(SDL_Window));
    if (!win) return NULL;
    win->real_screen = real;
    return win;
}

static inline void SDL_DestroyWindow(SDL_Window *win) {
    if (win) {
        free(win);
    }
}

/* Renderer creation: uses real_screen (320x240) directly to save 1.72 MB RAM and avoid per-frame downsampling */
static inline SDL_Renderer *SDL_CreateRenderer(SDL_Window *window, int index, Uint32 flags) {
    (void)index; (void)flags;
    if (!window || !window->real_screen) return NULL;

    SDL_Renderer *r = (SDL_Renderer *)malloc(sizeof(SDL_Renderer));
    if (!r) return NULL;

    r->real_screen = window->real_screen;
    r->virt_screen = r->real_screen;

    fprintf(stderr, "info: Direct scaled renderer: %dx%d @ %d bpp (1120x768 virt_screen eliminated)\n",
            r->real_screen->w, r->real_screen->h, r->real_screen->format->BitsPerPixel);

    r->draw_r = 0; r->draw_g = 0; r->draw_b = 0; r->draw_a = 255;
    r->draw_color = SDL_MapRGB(r->real_screen->format, 0, 0, 0);
    return r;
}

static inline void SDL_DestroyRenderer(SDL_Renderer *r) {
    if (r) {
        if (r->virt_screen && r->virt_screen != r->real_screen) {
            SDL_FreeSurface(r->virt_screen);
        }
        free(r);
    }
}

static inline int SDL_SetRenderDrawColor(SDL_Renderer *r, Uint8 red, Uint8 green, Uint8 blue, Uint8 alpha) {
    if (!r || !r->real_screen) return -1;
    r->draw_r = red;
    r->draw_g = green;
    r->draw_b = blue;
    r->draw_a = alpha;
    r->draw_color = SDL_MapRGB(r->real_screen->format, red, green, blue);
    return 0;
}

static inline int SDL_RenderClear(SDL_Renderer *r) {
    if (!r || !r->real_screen) return -1;
    return SDL_FillRect(r->real_screen, NULL, r->draw_color);
}

static inline int SDL_RenderFillRect(SDL_Renderer *r, const SDL_Rect *rect) {
    if (!r || !r->real_screen) return -1;
    if (!rect) {
        return SDL_FillRect(r->real_screen, NULL, r->draw_color);
    }
    int x0 = (rect->x * 2) / 7;
    int x1 = ((rect->x + rect->w) * 2 + 6) / 7;
    int y0 = (rect->y * 5) / 16;
    int y1 = ((rect->y + rect->h) * 5 + 15) / 16;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > r->real_screen->w) x1 = r->real_screen->w;
    if (y1 > r->real_screen->h) y1 = r->real_screen->h;
    if (x0 >= x1 || y0 >= y1) return 0;
    SDL_Rect dst = { (Sint16)x0, (Sint16)y0, (Uint16)(x1 - x0), (Uint16)(y1 - y0) };
    return SDL_FillRect(r->real_screen, &dst, r->draw_color);
}

/* Direct scaled copy: maps 1120x768 virtual coordinates directly to 320x240 16bpp real_screen */
static inline int SDL_RenderCopy(SDL_Renderer *r, SDL_Texture *texture, const SDL_Rect *srcrect, const SDL_Rect *dstrect) {
    if (!r || !r->real_screen || !texture) return -1;

    SDL_Surface *s = texture;
    SDL_Surface *d = r->real_screen;

    int src_x = srcrect ? srcrect->x : 0;
    int src_y = srcrect ? srcrect->y : 0;
    int src_w = srcrect ? srcrect->w : s->w;
    int src_h = srcrect ? srcrect->h : s->h;

    int dst_x = dstrect ? dstrect->x : 0;
    int dst_y = dstrect ? dstrect->y : 0;
    int dst_w = dstrect ? dstrect->w : 1120;
    int dst_h = dstrect ? dstrect->h : 768;

    if (dst_w <= 0 || dst_h <= 0 || src_w <= 0 || src_h <= 0) {
        return 0;
    }

    if (s->format->BytesPerPixel != 2 || d->format->BytesPerPixel != 2) {
        SDL_Rect src_copy = { (Sint16)src_x, (Sint16)src_y, (Uint16)src_w, (Uint16)src_h };
        SDL_Rect dst_scaled = {
            (Sint16)((dst_x * 2) / 7),
            (Sint16)((dst_y * 5) / 16),
            (Uint16)((dst_w * 2) / 7),
            (Uint16)((dst_h * 5) / 16)
        };
        return SDL_BlitSurface(s, &src_copy, d, &dst_scaled);
    }

    int dx0 = (dst_x * 2) / 7;
    int dx1 = ((dst_x + dst_w) * 2 + 6) / 7;
    int dy0 = (dst_y * 5) / 16;
    int dy1 = ((dst_y + dst_h) * 5 + 15) / 16;

    if (dx0 < 0) dx0 = 0;
    if (dy0 < 0) dy0 = 0;
    if (dx1 > d->w) dx1 = d->w;
    if (dy1 > d->h) dy1 = d->h;
    if (dx0 >= dx1 || dy0 >= dy1) {
        return 0;
    }

    int use_ck = ((s->flags & SDL_SRCCOLORKEY) != 0);
    uint16_t ck = (uint16_t)s->format->colorkey;
    int same_w = (src_w == dst_w);
    int same_h = (src_h == dst_h);

    if (SDL_MUSTLOCK(s)) SDL_LockSurface(s);
    if (SDL_MUSTLOCK(d)) SDL_LockSurface(d);

    if (use_ck) {
        for (int y = dy0; y < dy1; y++) {
            int vy0 = (y * 16) / 5;
            int vy1 = ((y + 1) * 16) / 5;
            if (vy1 <= vy0) vy1 = vy0 + 1;
            if (vy1 <= dst_y || vy0 >= dst_y + dst_h) continue;
            if (vy0 < dst_y) vy0 = dst_y;
            if (vy1 > dst_y + dst_h) vy1 = dst_y + dst_h;

            uint16_t *srows[4];
            int n_vy = 0;
            for (int vy = vy0; vy < vy1 && n_vy < 4; vy++) {
                int sy = same_h ? (src_y + (vy - dst_y)) : (src_y + (vy - dst_y) * src_h / dst_h);
                if (sy < 0) sy = 0;
                if (sy >= s->h) sy = s->h - 1;
                srows[n_vy++] = (uint16_t *)((uint8_t *)s->pixels + sy * s->pitch);
            }
            if (n_vy == 0) continue;

            uint16_t *drow = (uint16_t *)((uint8_t *)d->pixels + y * d->pitch);
            for (int x = dx0; x < dx1; x++) {
                int vx0 = (x * 7) >> 1;
                int vx1 = ((x + 1) * 7) >> 1;
                if (vx1 <= vx0) vx1 = vx0 + 1;
                if (vx1 <= dst_x || vx0 >= dst_x + dst_w) continue;
                if (vx0 < dst_x) vx0 = dst_x;
                if (vx1 > dst_x + dst_w) vx1 = dst_x + dst_w;

                int sx0 = same_w ? (src_x + (vx0 - dst_x)) : (src_x + (vx0 - dst_x) * src_w / dst_w);
                if (sx0 < 0) sx0 = 0;
                if (sx0 >= s->w) sx0 = s->w - 1;

                uint16_t px = ck;
                for (int ri = 0; ri < n_vy; ri++) {
                    uint16_t cand = srows[ri][sx0];
                    if (cand != ck) {
                        px = cand;
                        break;
                    }
                }
                if (px == ck && vx1 > vx0 + 1) {
                    int sx1 = same_w ? (src_x + (vx1 - 1 - dst_x)) : (src_x + (vx1 - 1 - dst_x) * src_w / dst_w);
                    if (sx1 < 0) sx1 = 0;
                    if (sx1 >= s->w) sx1 = s->w - 1;
                    for (int ri = 0; ri < n_vy; ri++) {
                        uint16_t cand = srows[ri][sx1];
                        if (cand != ck) {
                            px = cand;
                            break;
                        }
                    }
                }
                if (px != ck) {
                    drow[x] = px;
                }
            }
        }
    } else {
        for (int y = dy0; y < dy1; y++) {
            int vy = (y * 16) / 5;
            if (vy < dst_y || vy >= dst_y + dst_h) continue;
            int sy = same_h ? (src_y + (vy - dst_y)) : (src_y + (vy - dst_y) * src_h / dst_h);
            if (sy < 0) sy = 0;
            if (sy >= s->h) sy = s->h - 1;

            uint16_t *srow = (uint16_t *)((uint8_t *)s->pixels + sy * s->pitch);
            uint16_t *drow = (uint16_t *)((uint8_t *)d->pixels + y * d->pitch);

            for (int x = dx0; x < dx1; x++) {
                int vx = (x * 7) >> 1;
                if (vx < dst_x || vx >= dst_x + dst_w) continue;
                int sx = same_w ? (src_x + (vx - dst_x)) : (src_x + (vx - dst_x) * src_w / dst_w);
                if (sx < 0) sx = 0;
                if (sx >= s->w) sx = s->w - 1;
                drow[x] = srow[sx];
            }
        }
    }

    if (SDL_MUSTLOCK(d)) SDL_UnlockSurface(d);
    if (SDL_MUSTLOCK(s)) SDL_UnlockSurface(s);

    return 0;
}

/* Present 320x240 LCD surface directly */
static inline void SDL_RenderPresent(SDL_Renderer *r) {
    if (!r || !r->real_screen) return;
    SDL_UpdateRect(r->real_screen, 0, 0, 0, 0);
}

static inline int SDL_QueryTexture(SDL_Texture *texture, Uint32 *format, int *access, int *w, int *h) {
    if (!texture) return -1;
    if (format) *format = texture->format->BitsPerPixel;
    if (access) *access = 0;
    if (w) *w = texture->w;
    if (h) *h = texture->h;
    return 0;
}

static inline void SDL_DestroyTexture(SDL_Texture *texture) {
    if (texture) {
        SDL_FreeSurface(texture);
    }
}

/* Load texture once and pre-convert 32bpp RGBA to 16bpp RGB565 + ColorKey (eliminates runtime alpha blend) */
static inline SDL_Texture *IMG_LoadTexture(SDL_Renderer *r, const char *file) {
    (void)r;
    SDL_Surface *surf = IMG_Load(file);
    if (!surf) {
        fprintf(stderr, "error: IMG_Load failed for '%s': %s\n", file, IMG_GetError());
        return NULL;
    }

    SDL_Surface *conv = SDL_CreateRGBSurface(SDL_SWSURFACE, surf->w, surf->h, 16,
                                             0xF800, 0x07E0, 0x001F, 0);
    if (!conv) {
        fprintf(stderr, "warning: failed to allocate 16bpp surface for '%s', using raw surface\n", file);
        return surf;
    }

    if (SDL_MUSTLOCK(surf)) SDL_LockSurface(surf);
    if (SDL_MUSTLOCK(conv)) SDL_LockSurface(conv);

    int sbpp = surf->format->BytesPerPixel;
    int has_amask = (surf->format->Amask != 0);
    int has_ck = ((surf->flags & SDL_SRCCOLORKEY) != 0);
    Uint32 src_ck = surf->format->colorkey;
    const uint16_t ck16 = 0xF81F;
    int has_alpha = 0;

    for (int y = 0; y < surf->h; y++) {
        uint8_t *srow = (uint8_t *)surf->pixels + y * surf->pitch;
        uint16_t *drow = (uint16_t *)((uint8_t *)conv->pixels + y * conv->pitch);
        for (int x = 0; x < surf->w; x++) {
            Uint32 pixel = 0;
            uint8_t *p = srow + x * sbpp;
            if (sbpp == 1) {
                pixel = *p;
            } else if (sbpp == 2) {
                pixel = *(uint16_t *)p;
            } else if (sbpp == 3) {
#if SDL_BYTEORDER == SDL_BIG_ENDIAN
                pixel = ((Uint32)p[0] << 16) | ((Uint32)p[1] << 8) | (Uint32)p[2];
#else
                pixel = (Uint32)p[0] | ((Uint32)p[1] << 8) | ((Uint32)p[2] << 16);
#endif
            } else if (sbpp == 4) {
                pixel = *(uint32_t *)p;
            }

            Uint8 rc, gc, bc, ac;
            SDL_GetRGBA(pixel, surf->format, &rc, &gc, &bc, &ac);
            if ((has_amask && ac < 128) || (has_ck && pixel == src_ck)) {
                drow[x] = ck16;
                has_alpha = 1;
            } else {
                uint16_t c16 = (uint16_t)(((rc & 0xF8) << 8) | ((gc & 0xFC) << 3) | (bc >> 3));
                if (c16 == ck16) {
                    c16 = 0xF81E;
                }
                drow[x] = c16;
            }
        }
    }

    if (SDL_MUSTLOCK(conv)) SDL_UnlockSurface(conv);
    if (SDL_MUSTLOCK(surf)) SDL_UnlockSurface(surf);

    if (has_alpha) {
        SDL_SetColorKey(conv, SDL_SRCCOLORKEY, ck16);
    }

    SDL_FreeSurface(surf);
    return conv;
}

/* Audio compatibility */
static inline int Mix_PauseAudio(int pause_on) {
    if (pause_on) {
        Mix_Pause(-1);
    } else {
        Mix_Resume(-1);
    }
    return 0;
}

#ifdef __cplusplus
}
#endif

#endif /* SDL2_COMPAT_H */

