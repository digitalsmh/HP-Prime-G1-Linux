// Copyright 2024-2025 Viacheslav Chimishuk <vchimishuk@yandex.ru>
//
// This file is part of loderunner-ng.
//
// loderunner-ng is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// loderunner-ng is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with loderunner-ng. If not, see <http://www.gnu.org/licenses/>.

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <SDL2/SDL.h>
#include <SDL2/SDL_image.h>
#include "SDL2/SDL_mixer.h"
#include "config.h"
#include "exit.h"
#include "game.h"
#include "level.h"
#include "path.h"
#include "sound.h"
#include "texture.h"
#include "xmalloc.h"

#define SCREEN_WIDTH (MAP_WIDTH * TILE_MAP_WIDTH)
#define SCREEN_HEIGHT (MAP_HEIGHT * TILE_MAP_HEIGHT \
        + TILE_GROUND_HEIGHT + TILE_TEXT_HEIGHT)

#define FPS 23
#define FRAME_TIME (1000.0 / FPS)

#define PROG_NAME "loderunner-ng"

static void usage(void)
{
    fprintf(stderr, "usage: %s [-fm] [-l level] [-V volume]\n", PROG_NAME);
}

static void error(char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    fprintf(stderr, "%s: ", PROG_NAME);
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

static bool ignore_key(int key)
{
    // Any non-zero key or touch tap starts the game!
    return key == 0;
}

static bool quit_key(int key)
{
    return key == SDLK_q || key == 'q' || key == 'Q';
}

static int get_key(void)
{
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_KEYDOWN) {
            fprintf(stderr, "info: key pressed: %d ('%s')\n",
                    event.key.keysym.sym, SDL_GetKeyName(event.key.keysym.sym));
            return event.key.keysym.sym;
        }
        if (event.type == SDL_MOUSEBUTTONDOWN) {
            fprintf(stderr, "info: touchscreen tapped -> starting game!\n");
            return SDLK_RETURN;
        }
    }

    // Only check terminal stdin if it is an actual tty (avoids /dev/null EOF 100% CPU spin)
    if (isatty(STDIN_FILENO)) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(STDIN_FILENO, &fds);
        struct timeval tv = {0, 0};
        if (select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv) > 0) {
            char ch = 0;
            if (read(STDIN_FILENO, &ch, 1) > 0) {
                fprintf(stderr, "info: terminal key pressed: '%c' (%d)\n", ch, (int)ch);
                if (ch == '\n' || ch == '\r' || ch == ' ') return SDLK_RETURN;
                if (ch == 'q' || ch == 'Q') return SDLK_q;
                return SDLK_RETURN;
            }
        }
    }

    return 0;
}

// Render texture at the center of the virtual screen.
static void render_texture(SDL_Renderer *renderer, SDL_Texture *t)
{
    SDL_Rect src;
    src.x = 0;
    src.y = 0;
    int tw = 0, th = 0;
    SDL_QueryTexture(t, NULL, NULL, &tw, &th);
    src.w = tw;
    src.h = th;

    SDL_Rect dst;
    dst.x = (SCREEN_WIDTH - src.w) / 2;
    dst.y = (SCREEN_HEIGHT - src.h) / 2;
    dst.w = src.w;
    dst.h = src.h;

    if (SDL_RenderCopy(renderer, t, &src, &dst) < 0) {
        die("failed to render a texture: %s", SDL_GetError());
    }
}

// Load texture from file and render it to the screen.
// Starts immediately on any key, or automatically after timeout_ms milliseconds.
// Returns true if quit game key has been pressed.
static bool display_splash(SDL_Renderer *renderer, char *file, int timeout_ms)
{
    char *path = path_join(TEXTURES_DIR, file);
    if (access(path, R_OK) != 0) {
        fprintf(stderr, "warning: splash '%s' not found, skipping\n", path);
        free(path);
        return false;
    }
    free(path);

    fprintf(stderr, "info: loading splash '%s'...\n", file);

    SDL_Texture *t = texture_load(renderer, file);
    if (!t) {
        return false;
    }
    int k = 0;

    // If texture matches LCD resolution (320x240), blit directly to real LCD surface
    if (t->w == 320 && t->h == 240) {
        SDL_BlitSurface(t, NULL, renderer->real_screen, NULL);
        SDL_UpdateRect(renderer->real_screen, 0, 0, 0, 0);
        SDL_Flip(renderer->real_screen);
    } else {
        SDL_RenderClear(renderer);
        render_texture(renderer, t);
        SDL_RenderPresent(renderer);
    }

    fprintf(stderr, "info: displaying splash '%s' (%d ms timeout)...\n", file, timeout_ms);

    int frames = timeout_ms / 40;
    if (frames <= 0) frames = 1;

    for (int frame = 0; frame < frames; frame++) {
        k = get_key();
        if (k != 0) {
            break;
        }
        SDL_Delay(40);
    }
    SDL_DestroyTexture(t);
    fprintf(stderr, "info: splash '%s' finished, key=%d\n", file, k);

    return quit_key(k);
}

static bool display_file(SDL_Renderer *renderer, char *file)
{
    return display_splash(renderer, file, 1500);
}

static bool check_system_memory(void)
{
    FILE *fp;
    char line[256];

    /* Check 1: /proc/swaps must not contain /dev/loop0 (NAND swap livelock risk) */
    fp = fopen("/proc/swaps", "r");
    if (!fp) {
        fprintf(stderr, "실행 불가: /proc/swaps를 열 수 없습니다.\n");
        return false;
    }
    while (fgets(line, sizeof(line), fp)) {
        if (strstr(line, "/dev/loop0") != NULL) {
            fclose(fp);
            fprintf(stderr, "실행 불가: NAND 스왑(/dev/loop0)이 켜져 있어 커널 정지(livelock) 위험이 있습니다.\n");
            return false;
        }
    }
    fclose(fp);

    /* Check 2: vm.watermark_boost_factor must be 0 */
    int boost_factor = -1;
    fp = fopen("/proc/sys/vm/watermark_boost_factor", "r");
    if (fp) {
        if (fscanf(fp, "%d", &boost_factor) != 1) {
            boost_factor = -1;
        }
        fclose(fp);
    }
    if (boost_factor != 0) {
        fprintf(stderr, "실행 불가: vm.watermark_boost_factor(%d)가 0이 아닙니다 (4MB 워터마크 부스트 위험).\n", boost_factor);
        return false;
    }

    /* Check 3: /proc/meminfo available RAM + swap headroom */
    long mem_free = -1, mem_avail = -1, swap_free = -1;
    fp = fopen("/proc/meminfo", "r");
    if (!fp) {
        fprintf(stderr, "실행 불가: /proc/meminfo를 열 수 없습니다.\n");
        return false;
    }
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "MemFree:", 8) == 0) {
            sscanf(line + 8, "%ld", &mem_free);
        } else if (strncmp(line, "MemAvailable:", 13) == 0) {
            sscanf(line + 13, "%ld", &mem_avail);
        } else if (strncmp(line, "SwapFree:", 9) == 0) {
            sscanf(line + 9, "%ld", &swap_free);
        }
    }
    fclose(fp);

    if (mem_free < 0 || mem_avail < 0 || swap_free < 0) {
        fprintf(stderr, "실행 불가: /proc/meminfo 파싱 실패 (MemFree=%ld, MemAvailable=%ld, SwapFree=%ld).\n",
                mem_free, mem_avail, swap_free);
        return false;
    }

    if (mem_avail < 5500 || (mem_avail + swap_free) < 10000) {
        fprintf(stderr, "실행 불가: 메모리 여유 공간이 부족합니다 (현재 MemFree=%ld kB, MemAvailable=%ld kB, SwapFree=%ld kB, 합계=%ld kB / 필요: MemAvailable >= 5500 kB 및 합계 >= 10000 kB)\n",
                mem_free, mem_avail, swap_free, mem_avail + swap_free);
        return false;
    }

    fprintf(stderr, "info: 메모리 여유 확인 통과 (MemFree=%ld kB, MemAvailable=%ld kB, SwapFree=%ld kB, watermark_boost_factor=%d)\n",
            mem_free, mem_avail, swap_free, boost_factor);
    return true;
}

int main(int argc, char **argv)
{
    // Unbuffered logging for immediate real-time SSH / log file diagnostics
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    fprintf(stderr, "info: === LODE RUNNER NG STARTING (HP Prime G1) ===\n");

    if (!check_system_memory()) {
        return EXIT_FAILURE;
    }

    int ch;
    int start_level = 1;
    int volume = 100;
    Uint32 wflags = 0;

    // HP Prime G1 uses X11 display :0 by default
    if (!getenv("DISPLAY")) {
        setenv("DISPLAY", ":0", 1);
    }

    while ((ch = getopt(argc, argv, "fl:mV:")) != -1) {
        switch (ch) {
        case 'f':
            wflags |= SDL_WINDOW_FULLSCREEN;
            break;
        case 'l':
            errno = 0;
            start_level = (int) strtol(optarg, NULL, 10);
            if (errno != 0 || start_level < 1 || start_level > NLEVELS) {
                error("invalid level: %s", optarg);
                return EXIT_FAILURE;
            }
            break;
        case 'm':
            volume = 0;
            break;
        case 'V':
            errno = 0;
            volume = (int) strtol(optarg, NULL, 10);
            if (errno != 0 || volume < 0 || volume > 100) {
                error("invalid volume: %s", optarg);
                return EXIT_FAILURE;
            }
            break;
        default:
            usage();
            return EXIT_FAILURE;
        }
    }

    srandom(time(NULL));

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO) < 0) {
        if (SDL_Init(SDL_INIT_VIDEO) < 0) {
            die("failed to initialize SDL: %s", SDL_GetError());
        }
    }

    // Ignore touchscreen mouse motion events to prevent 100% CPU queue spin
    SDL_EventState(SDL_MOUSEMOTION, SDL_IGNORE);

    IMG_Init(IMG_INIT_PNG);

    int mxflags = MIX_INIT_OGG;
    Mix_Init(mxflags);

    if (Mix_OpenAudio(44100, AUDIO_S16SYS, 2, 2048) == -1) {
        fprintf(stderr, "warning: failed to open audio device: %s, running in silent mode\n", Mix_GetError());
        volume = 0;
    }

    SDL_Window *window = SDL_CreateWindow("Lode Runner",
        SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
        SCREEN_WIDTH, SCREEN_HEIGHT, wflags);
    if (window == NULL) {
        die("failed to create SDL window: %s", SDL_GetError());
    }

    // Grab input to ensure X11 routes matrix-keypad and touchscreen events directly to game
    SDL_WM_GrabInput(SDL_GRAB_ON);

    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "linear");

    SDL_Renderer *renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED);
    if (renderer == NULL) {
        die("failed to initialize SDL renderer: %s", SDL_GetError());
    }

    if (SDL_RenderSetScale(renderer, 1, 1) != 0) {
        die("failed to scale renderer: %s", SDL_GetError());
    }
    if (SDL_SetRenderDrawColor(renderer, 0x00, 0x00, 0x00, 255) != 0) {
        die("failed to set renderer color: %s", SDL_GetError());
    }

    texture_init(renderer);
    sound_init();
    sound_volume(volume);

    for (;;) {
        bool quit;

        // 1. 타이틀 스플래시 화면 (0.8초 또는 키/터치 시 즉시 전환)
        quit = display_splash(renderer, "start.png", 800);
        if (quit) {
            break;
        }

        // 2. 게임 키맵 설명 화면 (5초 또는 키/터치 시 즉시 시작)
        quit = display_splash(renderer, "controls.png", 5000);
        if (quit) {
            break;
        }

        struct level *lvl = level_init(start_level);
        struct game *game = game_init(lvl);
        bool pause = false;
        int key = 0;

        for (;;) {
            unsigned long stime = SDL_GetTicks64();
            int frame_key = 0;

            SDL_Event event;
            while (SDL_PollEvent(&event)) {
                switch (event.type) {
                case SDL_KEYDOWN:
                    fprintf(stderr, "info: KEYDOWN %d ('%s')\n",
                            event.key.keysym.sym, SDL_GetKeyName(event.key.keysym.sym));
                    fflush(stderr);
                    switch (event.key.keysym.sym) {
                    case SDLK_q:
                        quit = true;
                        goto eog;
                    case SDLK_ESCAPE:
                    case SDLK_p:
                        pause = !pause;
                        sound_pause();
                        break;
                    case SDLK_DELETE:
                    case SDLK_BACKSPACE:
                        key = SDLK_DELETE;
                        frame_key = SDLK_DELETE;
                        break;
                    case SDLK_LEFT:
                    case SDLK_4:
                    case SDLK_KP4:
                    case SDLK_a:
                        key = SDLK_LEFT;
                        frame_key = key;
                        break;
                    case SDLK_RIGHT:
                    case SDLK_6:
                    case SDLK_KP6:
                    case SDLK_d:
                        key = SDLK_RIGHT;
                        frame_key = key;
                        break;
                    case SDLK_UP:
                    case SDLK_8:
                    case SDLK_KP8:
                    case SDLK_w:
                        key = SDLK_UP;
                        frame_key = key;
                        break;
                    case SDLK_DOWN:
                    case SDLK_2:
                    case SDLK_KP2:
                    case SDLK_s:
                        key = SDLK_DOWN;
                        frame_key = key;
                        break;
                    case SDLK_f:
                    case SDLK_z:
                    case SDLK_7:
                    case SDLK_KP7:
                    case SDLK_j:
                    case SDLK_COMMA:
                        key = SDLK_z;
                        frame_key = key;
                        break;
                    case SDLK_g:
                    case SDLK_x:
                    case SDLK_9:
                    case SDLK_KP9:
                    case SDLK_k:
                    case SDLK_PERIOD:
                        key = SDLK_x;
                        frame_key = key;
                        break;
                    default:
                        key = event.key.keysym.sym;
                        frame_key = key;
                        break;
                    }
                    break;
                case SDL_KEYUP:
                    key = 0;
                    break;
                case SDL_MOUSEBUTTONDOWN:
                    fprintf(stderr, "info: TOUCH at (%d, %d)\n", event.button.x, event.button.y);
                    fflush(stderr);
                    if (event.button.x < 100) {
                        key = SDLK_LEFT;
                    } else if (event.button.x > 220) {
                        key = SDLK_RIGHT;
                    } else if (event.button.y < 80) {
                        key = SDLK_UP;
                    } else if (event.button.y > 160) {
                        key = SDLK_DOWN;
                    } else {
                        key = SDLK_z; // Center tap = Dig left
                    }
                    frame_key = key;
                    break;
                case SDL_MOUSEBUTTONUP:
                    key = 0;
                    break;
                case SDL_QUIT:
                    quit = true;
                    goto eog;
                }
            }

            // Check terminal stdin only if running from real tty
            if (isatty(STDIN_FILENO)) {
                fd_set fds;
                FD_ZERO(&fds);
                FD_SET(STDIN_FILENO, &fds);
                struct timeval tv = {0, 0};
                if (select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv) > 0) {
                    char ch = 0;
                    if (read(STDIN_FILENO, &ch, 1) > 0) {
                        if (ch == 'q' || ch == 'Q') { quit = true; goto eog; }
                        else if (ch == 127 || ch == '\b') { key = SDLK_DELETE; frame_key = SDLK_DELETE; }
                        else if (ch == 'p' || ch == 'P') { pause = !pause; }
                        else if (ch == 'a' || ch == 'A') { key = SDLK_LEFT; frame_key = key; }
                        else if (ch == 'd' || ch == 'D') { key = SDLK_RIGHT; frame_key = key; }
                        else if (ch == 'w' || ch == 'W') { key = SDLK_UP; frame_key = key; }
                        else if (ch == 's' || ch == 'S') { key = SDLK_DOWN; frame_key = key; }
                        else if (ch == 'f' || ch == 'F' || ch == 'z' || ch == 'Z') { key = SDLK_z; frame_key = key; }
                        else if (ch == 'g' || ch == 'G' || ch == 'x' || ch == 'X') { key = SDLK_x; frame_key = key; }
                    }
                }
            }

            int eff_key = (frame_key != 0) ? frame_key : key;
            bool tick_done = !pause && game_tick(game, eff_key);
            if (eff_key == SDLK_DELETE) {
                key = 0;
            }
            if (tick_done) {
                if (game->won) {
                    int l = lvl->num + 1;
                    int score = game->score;

                    if (l == NLEVELS) {
                        goto eog;
                    }

                    game_destroy(game);
                    level_destroy(lvl);

                    lvl = level_init(l);
                    game = game_init(lvl);
                    game_score(game, score);
                } else {
                    goto eog;
                }
            } else {
                SDL_RenderClear(renderer);
                game_render(game, renderer);
                if (pause) {
                    render_texture(renderer, texture_get(TEXTURE_PAUSED));
                }
                SDL_RenderPresent(renderer);
            }

            static unsigned long frame_cnt = 0;
            if (++frame_cnt % 46 == 0) {
                fprintf(stderr, "info: [GAME_TICK] frame=%lu runner=(%d,%d) score=%d guards=%d\n",
                        frame_cnt, game->runner->x, game->runner->y, game->score, game->nguards);
            }

            unsigned long elapsed = SDL_GetTicks64() - stime;
            if (elapsed < (unsigned long)FRAME_TIME) {
                unsigned long sleep_ms = (unsigned long)FRAME_TIME - elapsed;
                SDL_Delay(sleep_ms);
            }
        }

    eog:
        sound_stop();

        if (!quit) {
            if (!game->won) {
                quit = display_file(renderer, "gameover.png");
            }
        }

        game_destroy(game);
        level_destroy(lvl);

        if (quit) {
            break;
        }
    }

    texture_destroy();
    sound_destroy();

    SDL_FillRect(renderer->real_screen, NULL, 0);
    SDL_UpdateRect(renderer->real_screen, 0, 0, 0, 0);
    SDL_WM_GrabInput(SDL_GRAB_OFF);

    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    IMG_Quit();
    SDL_Quit();

    if (system("pidof xterm >/dev/null 2>&1 || DISPLAY=:0 xterm </dev/null >/dev/null 2>&1 &") != 0) {
        /* ignore */
    }

    return EXIT_SUCCESS;
}
