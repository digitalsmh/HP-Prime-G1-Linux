/*
 * hangul_automata.c — 2-Bulsik Korean Hangul Composition State Machine
 * Supports:
 *   - 19 Initial consonants (Choseong)
 *   - 21 Medial vowels (Jungseong) including 7 compound vowels (ㅘ,ㅙ,ㅚ,ㅝ,ㅞ,ㅟ,ㅢ)
 *   - 27 Final consonants (Jongseong) including 11 compound finals (ㄳ,ㄵ,ㄶ,ㄺ,ㄻ,ㄼ,ㄽ,ㄾ,ㄿ,ㅀ,ㅄ)
 *   - Dokkaebibul liaison splitting & step-by-step Jamo backspace decomposition
 */
#include "hangul_automata.h"

static const uint16_t CHO_TO_JAMO[19] = {
    0x3131, 0x3132, 0x3134, 0x3137, 0x3138, 0x3139, 0x3141, 0x3142,
    0x3143, 0x3145, 0x3146, 0x3147, 0x3148, 0x3149, 0x314A, 0x314B,
    0x314C, 0x314D, 0x314E
};

static const uint16_t JUNG_TO_JAMO[21] = {
    0x314F, 0x3150, 0x3151, 0x3152, 0x3153, 0x3154, 0x3155, 0x3156,
    0x3157, 0x3158, 0x3159, 0x315A, 0x315B, 0x315C, 0x315D, 0x315E,
    0x315F, 0x3160, 0x3161, 0x3162, 0x3163
};

static const uint16_t JONG_TO_JAMO[28] = {
    0x0000,
    0x3131, 0x3132, 0x3133, 0x3134, 0x3135, 0x3136, 0x3137,
    0x3139, 0x313A, 0x313B, 0x313C, 0x313D, 0x313E, 0x313F,
    0x3140, 0x3141, 0x3142, 0x3144, 0x3145, 0x3146, 0x3147,
    0x3148, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E
};

static int jamo_to_cho(uint32_t jamo)
{
    for (int i = 0; i < 19; i++) {
        if (CHO_TO_JAMO[i] == jamo)
            return i;
    }
    return -1;
}

static int jamo_to_jung(uint32_t jamo)
{
    for (int i = 0; i < 21; i++) {
        if (JUNG_TO_JAMO[i] == jamo)
            return i;
    }
    return -1;
}

static int jamo_to_jong(uint32_t jamo)
{
    for (int i = 1; i < 28; i++) {
        if (JONG_TO_JAMO[i] == jamo)
            return i;
    }
    return 0;
}

static int combine_jung(int v1, int v2)
{
    /* ㅗ(8) + ㅏ(0)/ㅐ(1)/ㅣ(20) -> ㅘ(9)/ㅙ(10)/ㅚ(11) */
    if (v1 == 8) {
        if (v2 == 0)  return 9;
        if (v2 == 1)  return 10;
        if (v2 == 20) return 11;
    }
    /* ㅜ(13) + ㅓ(4)/ㅔ(5)/ㅣ(20) -> ㅝ(14)/ㅞ(15)/ㅟ(16) */
    if (v1 == 13) {
        if (v2 == 4)  return 14;
        if (v2 == 5)  return 15;
        if (v2 == 20) return 16;
    }
    /* ㅡ(18) + ㅣ(20) -> ㅢ(19) */
    if (v1 == 18 && v2 == 20)
        return 19;
    return -1;
}

static int combine_jong(int j1, int cho2)
{
    /* j1 is single jongseong, cho2 is newly typed consonant */
    uint32_t c2 = CHO_TO_JAMO[cho2];
    if (j1 == 1 && c2 == 0x3145) return 3;  /* ㄱ + ㅅ -> ㄳ */
    if (j1 == 4) {
        if (c2 == 0x3148) return 5;         /* ㄴ + ㅈ -> ㄵ */
        if (c2 == 0x314E) return 6;         /* ㄴ + ㅎ -> ㄶ */
    }
    if (j1 == 8) {
        if (c2 == 0x3131) return 9;         /* ㄹ + ㄱ -> ㄺ */
        if (c2 == 0x3141) return 10;        /* ㄹ + ㅁ -> ㄻ */
        if (c2 == 0x3142) return 11;        /* ㄹ + ㅂ -> ㄼ */
        if (c2 == 0x3145) return 12;        /* ㄹ + ㅅ -> ㄽ */
        if (c2 == 0x314C) return 13;        /* ㄹ + ㅌ -> ㄾ */
        if (c2 == 0x314D) return 14;        /* ㄹ + ㅍ -> ㄿ */
        if (c2 == 0x314E) return 15;        /* ㄹ + ㅎ -> ㅀ */
    }
    if (j1 == 17 && c2 == 0x3145) return 18; /* ㅂ + ㅅ -> ㅄ */
    return 0;
}

static void split_jong_to_cho(int jong, int jong_first, int *remain_jong, int *next_cho)
{
    if (jong_first > 0) {
        *remain_jong = jong_first;
        uint32_t second_jamo = 0;
        switch (jong) {
        case 3:  second_jamo = 0x3145; break; /* ㄳ -> ㅅ */
        case 5:  second_jamo = 0x3148; break; /* ㄵ -> ㅈ */
        case 6:  second_jamo = 0x314E; break; /* ㄶ -> ㅎ */
        case 9:  second_jamo = 0x3131; break; /* ㄺ -> ㄱ */
        case 10: second_jamo = 0x3141; break; /* ㄻ -> ㅁ */
        case 11: second_jamo = 0x3142; break; /* ㄼ -> ㅂ */
        case 12: second_jamo = 0x3145; break; /* ㄽ -> ㅅ */
        case 13: second_jamo = 0x314C; break; /* ㄾ -> ㅌ */
        case 14: second_jamo = 0x314D; break; /* ㄿ -> ㅍ */
        case 15: second_jamo = 0x314E; break; /* ㅀ -> ㅎ */
        case 18: second_jamo = 0x3145; break; /* ㅄ -> ㅅ */
        default: second_jamo = JONG_TO_JAMO[jong]; break;
        }
        *next_cho = jamo_to_cho(second_jamo);
    } else {
        *remain_jong = 0;
        *next_cho = jamo_to_cho(JONG_TO_JAMO[jong]);
    }
}

void hangul_init(hangul_state_t *st)
{
    st->cho = -1;
    st->jung = -1;
    st->jung_first = -1;
    st->jong = 0;
    st->jong_first = 0;
}

bool hangul_is_empty(const hangul_state_t *st)
{
    return (st->cho < 0 && st->jung < 0);
}

uint32_t hangul_current_codepoint(const hangul_state_t *st)
{
    if (st->cho >= 0 && st->jung >= 0) {
        return 0xAC00U + (uint32_t)((st->cho * 21 + st->jung) * 28 + st->jong);
    }
    if (st->cho >= 0) {
        return CHO_TO_JAMO[st->cho];
    }
    if (st->jung >= 0) {
        return JUNG_TO_JAMO[st->jung];
    }
    return 0;
}

int hangul_flush(hangul_state_t *st, uint32_t *committed_out)
{
    uint32_t cp = hangul_current_codepoint(st);
    hangul_init(st);
    if (cp != 0) {
        *committed_out = cp;
        return 1;
    }
    return 0;
}

bool hangul_backspace(hangul_state_t *st)
{
    if (hangul_is_empty(st))
        return false;

    if (st->jong > 0) {
        if (st->jong_first > 0) {
            st->jong = st->jong_first;
            st->jong_first = 0;
        } else {
            st->jong = 0;
        }
        return true;
    }
    if (st->jung >= 0) {
        if (st->jung_first >= 0) {
            st->jung = st->jung_first;
            st->jung_first = -1;
        } else {
            st->jung = -1;
        }
        return true;
    }
    if (st->cho >= 0) {
        st->cho = -1;
        return true;
    }
    return false;
}

int hangul_feed_jamo(hangul_state_t *st, uint32_t jamo, uint32_t committed_out[2])
{
    int cho = jamo_to_cho(jamo);
    int jung = jamo_to_jung(jamo);

    /* Case 1: Consonant input */
    if (cho >= 0) {
        if (st->cho < 0 && st->jung < 0) {
            st->cho = cho;
            return 0;
        }
        if (st->cho >= 0 && st->jung < 0) {
            /* Standalone consonant followed by consonant -> commit first, start new */
            committed_out[0] = CHO_TO_JAMO[st->cho];
            hangul_init(st);
            st->cho = cho;
            return 1;
        }
        if (st->cho < 0 && st->jung >= 0) {
            /* Standalone vowel followed by consonant -> commit vowel, start new */
            committed_out[0] = JUNG_TO_JAMO[st->jung];
            hangul_init(st);
            st->cho = cho;
            return 1;
        }
        /* st->cho >= 0 && st->jung >= 0 */
        if (st->jong == 0) {
            int j = jamo_to_jong(jamo);
            if (j > 0) {
                st->jong = j;
                st->jong_first = 0;
                return 0;
            }
            /* Cannot be final consonant (ㅃ, ㅉ, ㄸ) -> commit syllable, start new */
            committed_out[0] = hangul_current_codepoint(st);
            hangul_init(st);
            st->cho = cho;
            return 1;
        } else {
            /* Already has jongseong: try compound jongseong if currently single */
            if (st->jong_first == 0) {
                int cj = combine_jong(st->jong, cho);
                if (cj > 0) {
                    st->jong_first = st->jong;
                    st->jong = cj;
                    return 0;
                }
            }
            committed_out[0] = hangul_current_codepoint(st);
            hangul_init(st);
            st->cho = cho;
            return 1;
        }
    }

    /* Case 2: Vowel input */
    if (jung >= 0) {
        if (st->cho < 0 && st->jung < 0) {
            st->jung = jung;
            return 0;
        }
        if (st->cho >= 0 && st->jung < 0) {
            st->jung = jung;
            st->jung_first = -1;
            return 0;
        }
        if (st->cho < 0 && st->jung >= 0) {
            if (st->jung_first < 0) {
                int cv = combine_jung(st->jung, jung);
                if (cv >= 0) {
                    st->jung_first = st->jung;
                    st->jung = cv;
                    return 0;
                }
            }
            committed_out[0] = JUNG_TO_JAMO[st->jung];
            hangul_init(st);
            st->jung = jung;
            return 1;
        }
        /* st->cho >= 0 && st->jung >= 0 */
        if (st->jong == 0) {
            if (st->jung_first < 0) {
                int cv = combine_jung(st->jung, jung);
                if (cv >= 0) {
                    st->jung_first = st->jung;
                    st->jung = cv;
                    return 0;
                }
            }
            committed_out[0] = hangul_current_codepoint(st);
            hangul_init(st);
            st->jung = jung;
            return 1;
        } else {
            /* Liaison (Dokkaebibul): move single jong or second of compound jong to next cho */
            int remain_jong = 0, next_cho = -1;
            split_jong_to_cho(st->jong, st->jong_first, &remain_jong, &next_cho);
            st->jong = remain_jong;
            st->jong_first = 0;
            committed_out[0] = hangul_current_codepoint(st);
            hangul_init(st);
            st->cho = next_cho;
            st->jung = jung;
            return 1;
        }
    }

    return 0;
}

