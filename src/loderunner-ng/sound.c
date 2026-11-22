// Copyright 2024-2025 Viacheslav Chimishuk <vchimishuk@yandex.ru>
// Modified for HP Prime G1 Linux port
#include "SDL2/SDL_mixer.h"
#include "config.h"
#include "exit.h"
#include "path.h"
#include "sound.h"
#include <stdio.h>
#include <stdlib.h>

Mix_Music *sounds[SOUND_X_SIZE];
static bool audio_ready = false;

static char *file_names[] = {
    "dead.ogg",    // SOUND_DEAD
    "dig.ogg",     // SOUND_DIG
    "down.ogg",    // SOUND_DOWN
    "fall.ogg",    // SOUND_FALL
    "finish.ogg",  // SOUND_FINISH
    "gold.ogg",    // SOUND_GOLD
    "hladder.ogg", // SOUND_HLADDER
    "reborn.ogg",  // SOUND_REBORN
    "trap.ogg",    // SOUND_TRAP
};

static Mix_Music *load(enum sound snd)
{
    char *f = path_join(SOUNDS_DIR, file_names[snd]);
    Mix_Music *m = Mix_LoadMUS(f);
    if (m == NULL) {
        // Non-fatal: calculate can still play silently
        // fprintf(stderr, "warning: cannot load sound %s\n", f);
    }
    free(f);
    return m;
}

void sound_init(void)
{
    // If audio device failed to open or muted, skip sound loading completely
    audio_ready = false;
    for (int i = 0; i < SOUND_X_SIZE; i++) {
        sounds[i] = NULL;
    }
}

void sound_destroy(void)
{
    for (int i = 0; i < SOUND_X_SIZE; i++) {
        if (sounds[i]) {
            Mix_FreeMusic(sounds[i]);
            sounds[i] = NULL;
        }
    }
    Mix_Quit();
    audio_ready = false;
}

void sound_pause(void)
{
    static int paused = 0;
    paused = !paused;
    Mix_PauseAudio(paused);
}

void sound_play(enum sound snd)
{
    if (audio_ready && sounds[snd]) {
        Mix_PlayMusic(sounds[snd], 0);
    }
}

void sound_stop(void)
{
    if (audio_ready) {
        Mix_HaltMusic();
    }
}

void sound_volume(int v)
{
    if (audio_ready) {
        Mix_VolumeMusic(v / 100.0 * MIX_MAX_VOLUME);
    }
}
